import threading

from megooflags._delivery.store import Snapshot, Store
from megooflags._engine import parse_ruleset

RULESET = parse_ruleset(
    {"schema_version": 1, "project": "p", "environment": "e", "flags": {}, "segments": {}}
)


def test_load_before_swap_is_none() -> None:
    assert Store().load() is None


def test_swap_installs_the_snapshot_and_returns_the_old_one() -> None:
    store = Store()
    first = Snapshot(RULESET, '"v1"', "bootstrap")
    second = Snapshot(RULESET, '"v2"', "stream")
    assert store.swap(first) is None
    assert store.load() is first
    assert store.swap(second) is first
    assert store.load() is second


def test_snapshot_is_immutable() -> None:
    snap = Snapshot(RULESET, "", "cache")
    try:
        snap.etag = "x"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("snapshot was modified")


def test_concurrent_swaps_return_each_snapshot_once() -> None:
    store = Store()
    snaps = [Snapshot(RULESET, str(i), "poll") for i in range(200)]
    olds: list[Snapshot | None] = []
    lock = threading.Lock()

    def swap(s: Snapshot) -> None:
        old = store.swap(s)
        with lock:
            olds.append(old)

    threads = [threading.Thread(target=swap, args=(s,)) for s in snaps]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = store.load()
    assert olds.count(None) == 1
    assert sorted(id(o) for o in olds if o is not None) == sorted(
        id(s) for s in snaps if s is not final
    )
