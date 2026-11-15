"""The MegooFlags OpenFeature provider: start-up order, lifecycle, provider events, fork handling
and the mapping of engine results to resolution details."""

from __future__ import annotations

import logging
import os
import threading
import time
import weakref
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, TypeVar

from openfeature.evaluation_context import EvaluationContext
from openfeature.event import ProviderEvent, ProviderEventDetails
from openfeature.exception import ErrorCode, GeneralError, ProviderFatalError
from openfeature.flag_evaluation import FlagResolutionDetails, FlagValueType, Reason
from openfeature.provider import AbstractProvider, Metadata

from megooflags._delivery.cache import Cache, CacheError
from megooflags._delivery.store import Snapshot, Store
from megooflags._delivery.sync import Status, Sync, Tuning
from megooflags._engine import Resolution, Ruleset, evaluate
from megooflags._events.aggregate import Evaluation
from megooflags._events.batcher import Batcher
from megooflags._transport import FatalError, sdk_url
from megooflags.config import Duration, MegooFlagsConfig, PathLike, config_error, make_config

T = TypeVar("T")
ObjectValue = Sequence[FlagValueType] | Mapping[str, FlagValueType]

# Bounds the final events flush.
_SHUTDOWN_TIMEOUT = 5.0

# Shorter Sync timings; only tests set it.
_sync_tuning: Tuning | None = None

# Started providers that are not shut down; a forked child restarts their threads.
_live: weakref.WeakSet[MegooFlagsProvider] = weakref.WeakSet()
_fork_hook_lock = threading.Lock()
_fork_hook_registered = False


def _register_fork_hook() -> None:
    """Register the after-fork hook once per process."""
    global _fork_hook_registered
    with _fork_hook_lock:
        if _fork_hook_registered or not hasattr(os, "register_at_fork"):
            return
        os.register_at_fork(after_in_child=_after_fork_in_child)
        _fork_hook_registered = True


def _after_fork_in_child() -> None:
    for p in list(_live):
        p._restart_after_fork()


class MegooFlagsProvider(AbstractProvider):
    """Evaluates flags against the ruleset of the environment of a server SDK key, with no
    network call per evaluation. Construction never touches the network: it validates the
    options (an invalid config is logged, and every evaluation then returns the default with
    PROVIDER_FATAL) and loads the bootstrap file, then, when that gave no ruleset, the cache.
    Durations are seconds or timedelta."""

    def __init__(
        self,
        *,
        sdk_key: str,
        base_url: str = "",
        bootstrap_file: PathLike | None = None,
        cache_dir: PathLike | None = None,
        offline: bool = False,
        init_timeout: Duration = 5.0,
        disable_stream: bool = False,
        poll_interval: Duration = 30.0,
        flush_interval: Duration = 30.0,
        send_context: bool = False,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__()
        cfg = make_config(
            sdk_key=sdk_key,
            base_url=base_url,
            bootstrap_file=bootstrap_file,
            cache_dir=cache_dir,
            offline=offline,
            init_timeout=init_timeout,
            disable_stream=disable_stream,
            poll_interval=poll_interval,
            flush_interval=flush_interval,
            send_context=send_context,
            logger=logger,
        )
        self._config = cfg
        self._logger = cfg.logger
        self._config_error = config_error(cfg)

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._started = False
        self._shut_down = False
        self._fatal: Exception | None = None
        self._init_timed_out = False  # initialize raised the timeout error
        self._stale = False  # STALE was emitted and not yet followed by READY

        self._store = Store()
        self._cache = Cache(cfg.cache_dir, cfg.sdk_key)
        self._sync: Sync | None = None
        self._batcher: Batcher | None = None
        if self._config_error is not None:
            self._logger.error("megooflags: invalid config: %s", self._config_error)
            return
        # Offline uses Sync only to apply the bootstrap file and the cache; it is never started.
        self._sync = self._new_sync()
        self._batcher = self._new_batcher()
        self._load()

    @property
    def config(self) -> MegooFlagsConfig:
        """The options with the defaults filled in."""
        return self._config

    def _new_sync(self) -> Sync:
        cfg = self._config
        return Sync(
            base_url=cfg.base_url,
            sdk_key=cfg.sdk_key,
            store=self._store,
            cache=self._cache,
            logger=self._logger,
            disable_stream=cfg.disable_stream,
            poll_interval=cfg.poll_interval,
            on_change=self._on_change,
            on_status=self._on_status,
            tuning=_sync_tuning,
        )

    def _new_batcher(self) -> Batcher | None:
        cfg = self._config
        if cfg.offline:
            return None
        return Batcher(
            url=sdk_url(cfg.base_url, "events"),
            sdk_key=cfg.sdk_key,
            logger=self._logger,
            flush_interval=cfg.flush_interval,
            send_context=cfg.send_context,
            on_fatal=lambda err: self._on_fatal(err, from_sync=False),
        )

    def _load(self) -> None:
        """Apply the bootstrap file, then, when there is still no ruleset, the cache."""
        assert self._sync is not None
        if self._config.bootstrap_file is not None:
            try:
                with open(self._config.bootstrap_file, "rb") as f:
                    raw = f.read()
            except OSError as err:
                self._logger.warning("megooflags: could not read the bootstrap file: %s", err)
            else:
                self._sync.apply(raw, "", "bootstrap")  # apply logs an invalid ruleset
        if self._store.load() is None:
            try:
                cached = self._cache.load()
            except (OSError, CacheError) as err:
                self._logger.warning("megooflags: could not read the ruleset cache: %s", err)
            else:
                if cached is not None:
                    self._sync.apply(cached.ruleset, cached.etag, "cache")

    # Lifecycle

    def get_metadata(self) -> Metadata:
        return Metadata(name="MegooFlags")

    def initialize(self, evaluation_context: EvaluationContext) -> None:
        """Start the event sender and, unless offline, the ruleset sync. With a ruleset from
        the bootstrap file or the cache, return at once; the network refreshes it in the
        background. Otherwise wait up to init_timeout for the first ruleset; on timeout raise,
        keep retrying and emit PROVIDER_READY when a ruleset arrives."""
        if self._config_error is not None:
            raise ProviderFatalError(self._config_error)
        with self._lock:
            if self._shut_down:
                raise GeneralError("megooflags: the provider was shut down")
            start = not self._started
            self._started = True
        if start:
            self._start()
        if self._store.load() is not None:
            return
        if self._config.offline:
            raise GeneralError(
                "megooflags: offline, and neither the bootstrap file nor the cache gave a "
                "valid ruleset"
            )
        timeout = self._config.init_timeout
        with self._cond:
            self._cond.wait_for(
                lambda: (
                    self._store.load() is not None or self._fatal is not None or self._shut_down
                ),
                timeout,
            )
            if self._store.load() is not None:
                return
            if self._fatal is not None:
                raise ProviderFatalError(str(self._fatal))
            if self._shut_down:
                raise GeneralError("megooflags: the provider was shut down")
            self._init_timed_out = True
        raise GeneralError(f"megooflags: no ruleset within {timeout:g}s")

    def _start(self) -> None:
        if self._batcher is not None:
            self._batcher.start()
        if not self._config.offline and self._sync is not None:
            self._sync.start()
            _live.add(self)
            _register_fork_hook()

    def shutdown(self) -> None:
        """Stop the sync and send the buffered events within 5 s. Idempotent. The last ruleset
        keeps being served."""
        with self._cond:
            if self._shut_down:
                return
            self._shut_down = True
            self._cond.notify_all()
        _live.discard(self)
        if self._sync is not None:
            self._sync.stop()
        if self._batcher is not None:
            sent = self._batcher.shutdown(_SHUTDOWN_TIMEOUT)
            if not sent and self._fatal is None:
                self._logger.warning("megooflags: could not send all events before shutdown")

    def _restart_after_fork(self) -> None:
        """In a forked child: new locks, a store with the parent's ruleset, and a new sync and
        event sender, whose buffers start empty. The parent's threads do not exist here, so the
        old objects are dropped without being stopped (stopping would shut down sockets the
        parent still uses)."""
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        if self._shut_down or self._fatal is not None:
            return
        current = self._store.load()
        self._store = Store()
        if current is not None:
            self._store.swap(current)
        self._stale = False
        self._sync = self._new_sync()
        self._batcher = self._new_batcher()
        self._start()

    # Sync and events callbacks

    def _on_change(self, old: Snapshot | None, new: Snapshot) -> None:
        """The first ruleset makes the provider ready; a later one is a configuration change."""
        if old is None:
            with self._cond:
                self._cond.notify_all()
                timed_out = self._init_timed_out
            if timed_out:
                self.emit_provider_ready(ProviderEventDetails(message="megooflags: ruleset loaded"))
            return
        self.emit_provider_configuration_changed(
            ProviderEventDetails(
                message="megooflags: ruleset updated",
                flags_changed=_flag_changes(old.ruleset, new.ruleset),
            )
        )

    def _on_status(self, status: Status, err: Exception | None) -> None:
        """Stale while disconnected after being ready, and ready again on reconnect."""
        if status == "stale":
            with self._lock:
                emit = not self._stale and self._store.load() is not None and self._fatal is None
                self._stale = self._stale or emit
            if emit:
                msg = "megooflags: disconnected; serving the last ruleset"
                if err is not None:
                    msg += f": {err}"
                self.emit_provider_stale(ProviderEventDetails(message=msg))
        elif status == "ready":
            with self._lock:
                emit = self._stale
                self._stale = False
            if emit:
                self.emit_provider_ready(ProviderEventDetails(message="megooflags: reconnected"))
        else:
            self._on_fatal(err, from_sync=True)

    def _on_fatal(self, err: Exception | None, *, from_sync: bool) -> None:
        """Handle a refused key once: stop sending events and the sync, emit PROVIDER_ERROR
        with the fatal code. The last ruleset keeps being served. Sync stops itself on a fatal
        status, and its callback runs on its own thread, so it is not stopped from there."""
        with self._cond:
            if self._fatal is not None:
                return
            fatal = err if err is not None else FatalError(reason="refused")
            self._fatal = fatal
            self._cond.notify_all()
        self._logger.error(
            "megooflags: the server refused the SDK key; serving the last ruleset: %s", fatal
        )
        if self._batcher is not None:
            self._batcher.stop()
        if not from_sync and self._sync is not None:
            self._sync.stop()
        _live.discard(self)
        self.emit_provider_error(
            ProviderEventDetails(message=str(fatal), error_code=ErrorCode.PROVIDER_FATAL)
        )

    def emit(self, event: ProviderEvent, details: ProviderEventDetails) -> None:
        """Emit an event unless the provider is shut down."""
        if not self._shut_down:
            super().emit(event, details)

    # Evaluation

    def resolve_boolean_details(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[bool]:
        got = self._evaluate(flag_key, default_value, evaluation_context, "boolean")
        if isinstance(got, FlagResolutionDetails):
            return got
        res, _ = got
        return _details(res, res.value if _served(res) else default_value)

    def resolve_string_details(
        self,
        flag_key: str,
        default_value: str,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[str]:
        got = self._evaluate(flag_key, default_value, evaluation_context, "string")
        if isinstance(got, FlagResolutionDetails):
            return got
        res, _ = got
        return _details(res, res.value if _served(res) else default_value)

    def resolve_integer_details(
        self,
        flag_key: str,
        default_value: int,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[int]:
        """A served number that is not integral is the default with TYPE_MISMATCH."""
        got = self._evaluate(flag_key, default_value, evaluation_context, "number")
        if isinstance(got, FlagResolutionDetails):
            return got
        res, ruleset = got
        if not _served(res):
            return _details(res, default_value)
        assert res.variant is not None
        variation = ruleset.flags[flag_key].variations[res.variant]
        if not variation.is_int:
            return FlagResolutionDetails(
                value=default_value,
                error_code=ErrorCode.TYPE_MISMATCH,
                error_message=f"flag '{flag_key}' is not an integer",
                reason=Reason.ERROR,
            )
        return _details(res, variation.int_value)

    def resolve_float_details(
        self,
        flag_key: str,
        default_value: float,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[float]:
        got = self._evaluate(flag_key, default_value, evaluation_context, "number")
        if isinstance(got, FlagResolutionDetails):
            return got
        res, _ = got
        return _details(res, float(res.value) if _served(res) else default_value)

    def resolve_object_details(
        self,
        flag_key: str,
        default_value: ObjectValue,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[ObjectValue]:
        got = self._evaluate(flag_key, default_value, evaluation_context, "json")
        if isinstance(got, FlagResolutionDetails):
            return got
        res, _ = got
        return _details(res, res.value if _served(res) else default_value)

    def _evaluate(
        self,
        flag_key: str,
        default: T,
        evaluation_context: EvaluationContext | None,
        expected_type: str,
    ) -> tuple[Resolution, Ruleset] | FlagResolutionDetails[T]:
        """Run the engine on the current ruleset, or the not-ready details when there is none.
        The context is copied, with the targeting key as key."""
        snap = self._store.load()
        if snap is None:
            return self._not_ready(default)
        context = _context(evaluation_context)
        res = evaluate(snap.ruleset, flag_key, context, default, expected_type)
        self._record(flag_key, res, context)
        return res, snap.ruleset

    def _record(self, flag_key: str, res: Resolution, context: dict[str, Any]) -> None:
        """Queue the evaluation for the event sender without blocking."""
        batcher = self._batcher
        if batcher is None:
            return
        key = context.get("key")
        batcher.record(
            Evaluation(
                flag_key=flag_key,
                variant=res.variant or "",
                served=res.variant is not None,
                reason=str(res.reason),
                context_key=key if isinstance(key, str) else "",
                at=time.time_ns() // 1_000_000,
                context=_json_safe(context) if self._config.send_context else None,
            )
        )

    def _not_ready(self, default: T) -> FlagResolutionDetails[T]:
        """The details while there is no ruleset."""
        if self._config_error is not None:
            code, msg = ErrorCode.PROVIDER_FATAL, self._config_error
        elif self._fatal is not None:
            code, msg = ErrorCode.PROVIDER_FATAL, "megooflags: the SDK key was refused"
        else:
            code, msg = ErrorCode.PROVIDER_NOT_READY, "megooflags: no ruleset is loaded yet"
        return FlagResolutionDetails(
            value=default, error_code=code, error_message=msg, reason=Reason.ERROR
        )


def _served(res: Resolution) -> bool:
    return res.error_code is None and res.variant is not None


def _details(res: Resolution, value: T) -> FlagResolutionDetails[T]:
    """Map an engine result to OpenFeature; PREREQUISITE_FAILED passes through as a reason."""
    return FlagResolutionDetails(
        value=value,
        error_code=ErrorCode(res.error_code) if res.error_code is not None else None,
        error_message=res.error_message,
        reason=str(res.reason),
        variant=res.variant,
        flag_metadata=res.metadata,
    )


def _plain(value: Any) -> Any:
    """value with OpenFeature's mappings and sequences turned into dicts and lists."""
    if isinstance(value, str | bytes):
        return value
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, Sequence):
        return [_plain(v) for v in value]
    return value


def _context(ec: EvaluationContext | None) -> dict[str, Any]:
    """The engine's context: the attributes, with key set to the targeting key when there is
    one. A datetime stays a datetime and matches no operator."""
    if ec is None:
        return {}
    context = {k: _plain(v) for k, v in ec.attributes.items()}
    if ec.targeting_key is not None:
        context["key"] = ec.targeting_key
    return context


def _json_safe(value: Any) -> Any:
    """A copy of the context that json.dumps accepts: dates become ISO 8601 text."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _flag_changes(old: Ruleset, new: Ruleset) -> list[str]:
    """The sorted keys of the flags that were added, removed or have a different version."""
    keys = [
        k for k, f in new.flags.items() if k not in old.flags or old.flags[k].version != f.version
    ]
    keys += [k for k in old.flags if k not in new.flags]
    return sorted(keys)
