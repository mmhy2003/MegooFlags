// Keeps the store current for the current context: the first values
// (bootstrap, cache, then the network), refreshes on stream changes, the
// polling fallback, context changes and the stop rules.
import 'dart:async';
import 'dart:math';

import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk.dart';

import 'backoff.dart';
import 'cache.dart';
import 'config.dart';
import 'context.dart';
import 'evaluator.dart';
import 'poller.dart';
import 'store.dart';
import 'streamer.dart';
import 'transport.dart';

/// Why start, refresh or setContext failed when no SdkException says it.
final class SyncException implements Exception {
  const SyncException(this.message);

  final String message;

  @override
  String toString() => message;
}

/// What the sync reports to the provider.
class SyncEvents {
  const SyncEvents({
    required this.ready,
    required this.stale,
    required this.changed,
    required this.fatal,
    required this.error,
  });

  /// Values arrived after start failed, or the stream is back after stale.
  final void Function() ready;

  /// The stream, connected before, keeps failing.
  final void Function() stale;

  /// A refresh changed these flags of the previous values.
  final void Function(List<String> flags) changed;

  /// The key was refused after start (FatalException or RevokedException);
  /// everything has stopped.
  final void Function(SdkException error) fatal;

  /// A refresh failed; it is retried.
  final void Function(Object error) error;
}

const _offlineMessage =
    'megooflags: offline mode needs bootstrap values or a cache entry';

/// A refresh() caller waiting for the first run numbered [run] or later.
typedef _Waiter = ({int run, Completer<void> done});

class Sync {
  /// [evaluator] is unused offline. [newStreamer] builds the streamer with
  /// the sync's hooks when delivery starts (never offline or with
  /// disableStream). [onFatalStop] runs when a refused key stops everything,
  /// so the provider can stop what the sync does not own. [random] jitters
  /// the retry backoff.
  Sync({
    required MegooFlagsConfig config,
    required Store store,
    required Cache cache,
    required Evaluator? evaluator,
    required Streamer Function(StreamerHooks hooks)? newStreamer,
    required SyncEvents events,
    required void Function() onFatalStop,
    Random? random,
  }) : _config = config,
       _store = store,
       _cache = cache,
       _evaluator = config.offline ? null : evaluator,
       _newStreamer = config.offline ? null : newStreamer,
       _events = events,
       _onFatalStop = onFatalStop,
       _log = config.logger,
       _backoff = Backoff(
         const Duration(seconds: 1),
         const Duration(seconds: 60),
         random,
       ) {
    _poller = Poller(config.pollInterval, refresh);
    _hooks = StreamerHooks(
      onConnected: _onConnected,
      onChanged: (_) => _ignore(refresh()),
      onFatal: _stopAll,
      onFailure: (consecutive, _) => _onStreamFailure(consecutive),
    );
  }

  final MegooFlagsConfig _config;
  final Store _store;
  final Cache _cache;
  final Evaluator? _evaluator;
  final Streamer Function(StreamerHooks hooks)? _newStreamer;
  final SyncEvents _events;
  final void Function() _onFatalStop;
  final MegooFlagsLogger _log;
  final Backoff _backoff;
  late final Poller _poller;
  late final StreamerHooks _hooks;
  Streamer? _streamer;

  Map<String, Object?> _body = const {};
  String _hash = contextHash(const {});

  /// Incremented by every context change; older answers are dropped.
  int _gen = 0;

  /// The number of evaluate runs started.
  int _runs = 0;
  bool _inFlight = false;
  bool _queued = false;
  List<_Waiter> _waiters = [];
  Timer? _retry;
  Future<void> _cacheOps = Future.value();

  /// Set once stopped or fatal: the error later refresh() calls fail with.
  Object? _halted;
  bool _starting = false;

  /// start failed without a fatal error: emit ready when values arrive.
  bool _readyPending = false;
  bool _deliveryStarted = false;
  bool _connected = false;
  bool _stale = false;
  bool _paused = false;
  bool _pollerWasRunning = false;

  /// The request context of the current evaluation context.
  Map<String, Object?> get body => _body;

  /// start serves bootstrap values, then a cache entry for [ctx]. With
  /// values it returns at once and refreshes in the background; without, it
  /// waits for the first refresh up to initTimeout. It fails with the
  /// FatalException of a refused key, or a SyncException or the refresh's
  /// error otherwise; refresh keeps retrying unless fatal.
  Future<void> start(EvaluationContext ctx) async {
    _starting = true;
    try {
      await _start(toRequestContext(ctx));
    } finally {
      _starting = false;
    }
  }

  Future<void> _start(Map<String, Object?> body) async {
    _setBody(body);
    final hash = _hash;
    final targetingKey = targetingKeyOf(body);
    final bootstrap = _config.bootstrap;
    if (bootstrap != null) {
      _store.current = StoredResults.fromList(
        bootstrap,
        etag: null,
        source: Source.bootstrap,
        contextHash: hash,
        targetingKey: targetingKey,
      );
    }
    final cached = await _cache.load(hash);
    if (cached != null && _hash == hash) {
      _store.current = StoredResults(
        results: cached.results,
        etag: cached.etag,
        source: Source.cache,
        contextHash: hash,
        targetingKey: targetingKey,
      );
    }
    if (_evaluator == null) {
      if (_store.current == null) throw const SyncException(_offlineMessage);
      return;
    }
    final first = refresh();
    unawaited(
      first.then(
        (_) => _startDelivery(),
        onError: (Object _) => _startDelivery(),
      ),
    );
    if (_store.current != null) return;
    final timeout = _config.initTimeout;
    try {
      await first.timeout(
        timeout,
        onTimeout: () => throw SyncException(
          'megooflags: no flag values within ${_seconds(timeout)}s',
        ),
      );
    } catch (_) {
      _readyPending = _halted == null;
      rethrow;
    }
  }

  /// refresh evaluates the current context. At most one evaluate is in
  /// flight and one queued; calls while one is queued share it. It completes
  /// when a run started after the call stores values for the current context
  /// and fails when that run fails (a retry follows unless fatal). Offline it
  /// completes at once.
  Future<void> refresh() {
    final halted = _halted;
    if (halted != null) return Future.error(halted);
    if (_evaluator == null) return Future.value();
    final done = Completer<void>();
    _waiters.add((run: _runs + 1, done: done));
    _kick();
    return done.future;
  }

  /// setContext switches to [ctx]. A new targeting key clears the store and
  /// the cache entry at once; other changes keep serving the old values
  /// until the new ones arrive. It completes or fails as refresh() does for
  /// the new context. Offline or stopped it fetches nothing, but a new
  /// targeting key still clears the store and the cache entry.
  Future<void> setContext(EvaluationContext ctx) {
    final body = toRequestContext(ctx);
    final keyChanged = targetingKeyOf(body) != targetingKeyOf(_body);
    _setBody(body);
    if (keyChanged) {
      _store.current = null;
      _cacheOp(_cache.remove);
    }
    if (_evaluator == null || _halted != null) return refresh();
    _gen++;
    // The evaluate in flight cannot be aborted; its answer is dropped and
    // it no longer holds the slot, so the new context runs at once.
    _inFlight = false;
    _queued = false;
    return refresh();
  }

  /// pause closes the stream and stops the poller (the app is hidden).
  void pause() {
    if (_halted != null || _paused) return;
    _paused = true;
    _pollerWasRunning = _poller.running;
    _poller.stop();
    _streamer?.pause();
  }

  /// resume reopens the stream, and the poller when it was running.
  void resume() {
    if (_halted != null || !_paused) return;
    _paused = false;
    if (!_deliveryStarted) {
      _startDelivery();
      return;
    }
    _streamer?.resume();
    if (_pollerWasRunning) _poller.start();
  }

  /// stop ends the stream, the poller and every refresh for good.
  void stop() => _halt(const SyncException('megooflags: provider shut down'));

  void _setBody(Map<String, Object?> body) {
    _body = body;
    _hash = contextHash(body);
  }

  void _kick() {
    if (_inFlight) {
      _queued = true;
      return;
    }
    unawaited(_run());
  }

  Future<void> _run() async {
    _retry?.cancel();
    _retry = null;
    final run = ++_runs;
    final gen = _gen;
    final body = _body;
    final hash = _hash;
    _inFlight = true;
    final stored = _store.current;
    final etag = stored?.contextHash == hash ? stored!.etag : null;
    EvaluateOutcome? outcome;
    Object? error;
    try {
      outcome = await _evaluator!.evaluate(body, etag);
    } catch (e) {
      error = e;
    }
    // A context change or a stop freed the slot meanwhile: drop the answer.
    if (gen != _gen || _halted != null) return;
    _inFlight = false;
    if (outcome != null) error = _apply(outcome, body, hash);
    if (error == null) {
      _settle(run);
    } else {
      _failed(run, error);
    }
    if (_queued && _halted == null) {
      _queued = false;
      unawaited(_run());
    }
  }

  /// _apply stores an answer for the current context; it returns an error
  /// for a 304 it cannot use.
  Object? _apply(
    EvaluateOutcome outcome,
    Map<String, Object?> body,
    String hash,
  ) {
    final old = _store.current;
    switch (outcome) {
      case EvaluateNotModified(:final etag):
        if (old == null || old.contextHash != hash) {
          return const InvalidResponseException(
            'unexpected 304 without values',
          );
        }
        _store.current = StoredResults(
          results: old.results,
          etag: etag ?? old.etag,
          source: Source.network,
          contextHash: old.contextHash,
          targetingKey: old.targetingKey,
        );
      case EvaluateOk(:final results, :final etag):
        final next = StoredResults.fromList(
          results,
          etag: etag,
          source: Source.network,
          contextHash: hash,
          targetingKey: targetingKeyOf(body),
        );
        _store.current = next;
        _cacheOp(() => _cache.save(next));
        if (_readyPending) {
          _readyPending = false;
          _call(_events.ready);
        } else if (old != null) {
          final changed = changedFlags(old, next);
          if (changed.isNotEmpty) _call(() => _events.changed(changed));
        }
    }
    _backoff.reset();
    return null;
  }

  void _failed(int run, Object err) {
    if (err is FatalException) {
      _stopAll(err);
      return;
    }
    _log(
      MegooFlagsLogLevel.debug,
      'megooflags: evaluate failed: ${_reason(err)}',
    );
    _call(() => _events.error(err));
    _settle(run, err);
    if (_queued) return;
    final wait = err is RetryAfterException ? err.wait : _backoff.next();
    _retry = Timer(wait, () {
      _retry = null;
      _kick();
    });
  }

  /// _settle completes, or fails with [err], the waiters served by [run].
  void _settle(int run, [Object? err]) {
    final served = [
      for (final w in _waiters)
        if (w.run <= run) w,
    ];
    _waiters = [
      for (final w in _waiters)
        if (w.run > run) w,
    ];
    for (final w in served) {
      if (err == null) {
        w.done.complete();
      } else {
        w.done.completeError(err);
      }
    }
  }

  void _startDelivery() {
    if (_halted != null || _deliveryStarted || _paused) return;
    _deliveryStarted = true;
    final newStreamer = _newStreamer;
    if (_config.disableStream || newStreamer == null) {
      _poller.start();
    } else {
      _streamer = newStreamer(_hooks)..start();
    }
  }

  void _onConnected() {
    if (_halted != null) return;
    _poller.stop();
    _connected = true;
    if (_stale) {
      _stale = false;
      _call(_events.ready);
    }
  }

  void _onStreamFailure(int consecutive) {
    if (_halted != null || consecutive < 2) return;
    if (!_paused) _poller.start();
    if (_connected && !_stale) {
      _stale = true;
      _call(_events.stale);
    }
  }

  /// _stopAll stops everything for a refused key and keeps the store. While
  /// start runs, its failure reports the error instead of a fatal event.
  void _stopAll(SdkException err) {
    if (_halted != null) return;
    _halt(err);
    _call(_onFatalStop);
    if (!_starting) _call(() => _events.fatal(err));
  }

  void _halt(Object err) {
    if (_halted != null) return;
    _halted = err;
    _inFlight = false;
    _retry?.cancel();
    _retry = null;
    _queued = false;
    _poller.stop();
    _streamer?.stop();
    final waiters = _waiters;
    _waiters = [];
    for (final w in waiters) {
      w.done.completeError(err);
    }
  }

  /// _cacheOp runs [op] after the previous cache operation, so writes keep
  /// their order. Cache operations never fail.
  void _cacheOp(Future<void> Function() op) {
    _cacheOps = _cacheOps.then((_) => op());
  }

  /// _call runs an event callback; an error it throws is logged.
  void _call(void Function() fn) {
    try {
      fn();
    } catch (e) {
      _log(
        MegooFlagsLogLevel.error,
        'megooflags: event callback failed: ${e.runtimeType}',
      );
    }
  }
}

/// _seconds writes [d] in seconds without a needless fraction: 5, 1.5.
String _seconds(Duration d) => d.inMilliseconds % 1000 == 0
    ? '${d.inSeconds}'
    : '${d.inMilliseconds / 1000}';

/// _reason describes a failure for the debug log: an SDK error's message
/// without the prefix, else the error's type (a message could hold a host
/// name).
String _reason(Object e) => e is SdkException
    ? e.message.replaceFirst('megooflags: ', '')
    : '${e.runtimeType}';

void _ignore(Future<void> f) => unawaited(f.then((_) {}, onError: (_) {}));
