import json
from datetime import UTC, datetime

from megooflags._events.aggregate import Aggregator, Batch, Count, Evaluation, Lru

T0 = int(datetime(2026, 11, 10, 9, 15, 2, tzinfo=UTC).timestamp() * 1000)
HOUR = 3_600_000


def served(flag: str, variant: str, key: str, at: int = T0) -> Evaluation:
    return Evaluation(
        flag_key=flag,
        variant=variant,
        served=True,
        reason="SPLIT",
        context_key=key,
        context={"key": key},
        at=at,
    )


def unserved(flag: str, key: str = "", at: int = T0) -> Evaluation:
    return Evaluation(
        flag_key=flag, variant="", served=False, reason="ERROR", context_key=key, at=at
    )


def agg(dedup: int = 100, pending: int = 100, send_context: bool = False) -> Aggregator:
    return Aggregator(dedup_size=dedup, max_pending=pending, send_context=send_context)


def count(flag: str, variant: str | None, hour: str, n: int) -> Count:
    return {"flag_key": flag, "variant": variant, "hour": hour, "count": n}


def test_lru_evicts_the_least_recently_seen_key() -> None:
    lru = Lru(2)
    assert lru.seen("a") is False
    assert lru.seen("b") is False
    assert lru.seen("a") is True
    # a is now the most recent, so inserting c evicts b.
    assert lru.seen("c") is False
    assert lru.seen("a") is True
    assert lru.seen("b") is False
    assert len(lru) == 2


def test_one_exposure_per_context_key_flag_variant_and_hour() -> None:
    a = agg()
    a.add(served("f", "on", "u1"))
    a.add(served("f", "on", "u1", T0 + 10 * 60_000))
    assert a.pending() == (1, 1)
    a.add(served("f", "on", "u1", T0 + HOUR))
    assert a.pending()[0] == 2

    for other in (served("f", "off", "u1"), served("f", "on", "u2"), served("g", "on", "u1")):
        b = agg()
        b.add(served("f", "on", "u1"))
        b.add(other)
        assert b.pending()[0] == 2


def test_anonymous_contexts_share_one_exposure() -> None:
    a = agg()
    a.add(served("f", "on", ""))
    a.add(served("f", "on", ""))
    assert a.pending()[0] == 1


def test_counts_add_up_per_flag_variant_and_hour() -> None:
    a = agg()
    for i in range(5):
        a.add(served("f", "on", f"u{i}"))
    a.add(served("f", "on", "u1", T0 + HOUR))
    a.add(served("f", "off", "u1"))
    assert a.pending()[1] == 3
    assert a.take(1000).counts == [
        count("f", "on", "2026-11-10T09:00:00Z", 5),
        count("f", "on", "2026-11-10T10:00:00Z", 1),
        count("f", "off", "2026-11-10T09:00:00Z", 1),
    ]
    assert a.pending() == (0, 0)


def test_unserved_evaluations_count_with_a_null_variant_and_no_exposure() -> None:
    a = agg()
    a.add(unserved("f", "u1"))
    a.add(unserved("f", "u2"))
    b = a.take(1000)
    assert b.exposures == []
    assert b.counts == [count("f", None, "2026-11-10T09:00:00Z", 2)]


def test_context_only_with_send_context() -> None:
    off = agg(send_context=False)
    off.add(served("f", "on", "u1"))
    assert "context" not in off.take(1000).exposures[0]

    on = agg(send_context=True)
    on.add(served("f", "on", "u1"))
    assert on.take(1000).exposures[0].get("context") == {"key": "u1"}


def test_exposure_fields_with_a_utc_millisecond_timestamp() -> None:
    a = agg()
    a.add(served("f", "on", "u1", T0 + 123))
    assert a.take(1000).exposures == [
        {
            "flag_key": "f",
            "variant": "on",
            "reason": "SPLIT",
            "timestamp": "2026-11-10T09:15:02.123Z",
        }
    ]


def test_hour_is_truncated_to_utc_without_fractions() -> None:
    a = agg()
    last_ms = int(datetime(2026, 11, 10, 9, 59, 59, tzinfo=UTC).timestamp() * 1000) + 999
    a.add(unserved("f", "", last_ms))
    assert a.take(10).counts[0]["hour"] == "2026-11-10T09:00:00Z"


def test_lru_forgets_the_oldest_key_beyond_the_dedup_size() -> None:
    a = agg(dedup=2)
    a.add(served("f", "on", "u1"))
    a.add(served("f", "on", "u2"))
    a.add(served("f", "on", "u3"))
    a.add(served("f", "on", "u1"))
    assert a.pending()[0] == 4


def test_max_pending_drops_exposures_but_never_counts() -> None:
    a = agg(pending=2)
    for i in range(5):
        a.add(served("f", "on", f"u{i}"))
    a.add(served("f", "on", "u0"))  # a duplicate is not a drop
    assert a.pending()[0] == 2
    assert a.dropped == 3
    assert a.take(1000).counts[0]["count"] == 6


def test_take_returns_at_most_the_limit_of_each_list_oldest_first() -> None:
    a = agg(dedup=10_000, pending=10_000)
    for i in range(1500):
        a.add(served(f"f{i}", "on", "u"))
    b = a.take(1000)
    assert (len(b.exposures), len(b.counts)) == (1000, 1000)
    assert b.exposures[0]["flag_key"] == "f0"
    assert b.counts[999]["flag_key"] == "f999"
    b = a.take(1000)
    assert (len(b.exposures), len(b.counts)) == (500, 500)
    assert b.exposures[0]["flag_key"] == "f1000"
    assert b.counts[0]["flag_key"] == "f1000"
    assert a.pending() == (0, 0)


def test_take_splits_a_count_over_1e9_into_several_items() -> None:
    a = agg(10, 10)
    a.add(served("f", "on", "u"))
    a.restore(Batch([], [count("f", "on", "2026-11-10T09:00:00Z", 2_499_999_999)]))
    assert a.take(0).counts == []
    assert [c["count"] for c in a.take(2).counts] == [1_000_000_000, 1_000_000_000]
    assert [c["count"] for c in a.take(2).counts] == [500_000_000]
    assert a.pending() == (0, 0)


def test_restore_puts_a_batch_in_front_and_merges_its_counts() -> None:
    a = agg()
    a.add(served("f", "on", "u1"))
    a.add(served("f", "on", "u2"))
    a.add(unserved("f"))
    sent = a.take(1000)

    a.add(served("f", "on", "u3", T0 + 5))
    a.add(served("g", "on", "u3", T0 + 6))
    a.restore(sent)

    b = a.take(1000)
    assert [(e["flag_key"], e["timestamp"]) for e in b.exposures] == [
        ("f", sent.exposures[0]["timestamp"]),
        ("f", sent.exposures[1]["timestamp"]),
        ("f", "2026-11-10T09:15:02.005Z"),
        ("g", "2026-11-10T09:15:02.006Z"),
    ]
    assert b.counts == [
        count("f", "on", "2026-11-10T09:00:00Z", 3),
        count("f", None, "2026-11-10T09:00:00Z", 1),
        count("g", "on", "2026-11-10T09:00:00Z", 1),
    ]


def test_restore_joins_the_items_of_a_split_count() -> None:
    a = agg(10, 10)
    a.restore(
        Batch(
            [],
            [
                count("f", "on", "2026-11-10T09:00:00Z", 1_000_000_000),
                count("f", "on", "2026-11-10T09:00:00Z", 5),
            ],
        )
    )
    assert a.pending() == (0, 1)
    assert [c["count"] for c in a.take(10).counts] == [1_000_000_000, 5]


def test_batch_serialises_to_the_body_of_post_sdk_v1_events() -> None:
    a = agg(10, 10, send_context=True)
    a.add(served("new-checkout", "on", "user-1"))
    a.add(unserved("new-checkout"))
    assert json.loads(a.take(10).body()) == {
        "exposures": [
            {
                "flag_key": "new-checkout",
                "variant": "on",
                "reason": "SPLIT",
                "timestamp": "2026-11-10T09:15:02.000Z",
                "context": {"key": "user-1"},
            }
        ],
        "counts": [
            count("new-checkout", "on", "2026-11-10T09:00:00Z", 1),
            count("new-checkout", None, "2026-11-10T09:00:00Z", 1),
        ],
    }
    assert agg().take(10).body() == b'{"exposures":[],"counts":[]}'


def test_body_is_utf8_without_ascii_escapes() -> None:
    a = agg(send_context=True)
    a.add(
        Evaluation(
            flag_key="f",
            variant="é",
            served=True,
            reason="STATIC",
            context_key="u",
            context={"name": "Zoë"},
            at=T0,
        )
    )
    body = a.take(10).body()
    assert "Zoë".encode() in body
    assert b"\\u" not in body
