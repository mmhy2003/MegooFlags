# frozen_string_literal: true

require_relative "../test_helper"
require "json"
require "time"
require "megooflags/internal/events/aggregator"

class AggregatorTest < Minitest::Test
  Events = MegooFlags::Internal::Events

  T0 = Time.utc(2026, 11, 10, 9, 15, 2).to_i * 1000
  HOUR = 3_600_000

  def served(flag, variant, key, at = T0)
    Events::Evaluation.new(flag_key: flag, variant: variant, served: true, reason: "SPLIT", context_key: key,
                           context: { "key" => key }, at: at)
  end

  def unserved(flag, key = "", at = T0)
    Events::Evaluation.new(flag_key: flag, variant: "", served: false, reason: "ERROR", context_key: key, at: at)
  end

  def agg(dedup: 100, pending: 100, send_context: false)
    Events::Aggregator.new(dedup_size: dedup, max_pending: pending, send_context: send_context)
  end

  def count(flag, variant, hour, total) = { "flag_key" => flag, "variant" => variant, "hour" => hour, "count" => total }

  def test_lru_evicts_the_least_recently_seen_key
    lru = Events::Lru.new(2)
    refute lru.seen?("a")
    refute lru.seen?("b")
    assert lru.seen?("a")
    # a is now the most recent, so inserting c evicts b.
    refute lru.seen?("c")
    assert lru.seen?("a")
    refute lru.seen?("b")
    assert_equal 2, lru.size
  end

  def test_one_exposure_per_context_key_flag_variant_and_hour
    a = agg
    a.add(served("f", "on", "u1"))
    a.add(served("f", "on", "u1", T0 + (10 * 60_000)))
    assert_equal [1, 1], a.pending
    a.add(served("f", "on", "u1", T0 + HOUR))
    assert_equal 2, a.pending[0]

    [served("f", "off", "u1"), served("f", "on", "u2"), served("g", "on", "u1")].each do |other|
      b = agg
      b.add(served("f", "on", "u1"))
      b.add(other)
      assert_equal 2, b.pending[0]
    end
  end

  def test_anonymous_contexts_share_one_exposure
    a = agg
    a.add(served("f", "on", ""))
    a.add(served("f", "on", ""))
    assert_equal 1, a.pending[0]
  end

  def test_counts_add_up_per_flag_variant_and_hour
    a = agg
    5.times { |i| a.add(served("f", "on", "u#{i}")) }
    a.add(served("f", "on", "u1", T0 + HOUR))
    a.add(served("f", "off", "u1"))
    assert_equal 3, a.pending[1]
    assert_equal [count("f", "on", "2026-11-10T09:00:00Z", 5), count("f", "on", "2026-11-10T10:00:00Z", 1),
                  count("f", "off", "2026-11-10T09:00:00Z", 1)], a.take(1000).counts
    assert_equal [0, 0], a.pending
  end

  def test_unserved_evaluations_count_with_a_null_variant_and_no_exposure
    a = agg
    a.add(unserved("f", "u1"))
    a.add(unserved("f", "u2"))
    b = a.take(1000)
    assert_empty b.exposures
    assert_equal [count("f", nil, "2026-11-10T09:00:00Z", 2)], b.counts
  end

  def test_context_only_with_send_context
    off = agg(send_context: false)
    off.add(served("f", "on", "u1"))
    refute off.take(1000).exposures[0].key?("context")

    on = agg(send_context: true)
    on.add(served("f", "on", "u1"))
    assert_equal({ "key" => "u1" }, on.take(1000).exposures[0]["context"])
  end

  def test_exposure_fields_with_a_utc_millisecond_timestamp
    a = agg
    a.add(served("f", "on", "u1", T0 + 123))
    assert_equal [{ "flag_key" => "f", "variant" => "on", "reason" => "SPLIT",
                    "timestamp" => "2026-11-10T09:15:02.123Z" }], a.take(1000).exposures
  end

  def test_hour_is_truncated_to_utc_without_fractions
    a = agg
    a.add(unserved("f", "", (Time.utc(2026, 11, 10, 9, 59, 59).to_i * 1000) + 999))
    assert_equal "2026-11-10T09:00:00Z", a.take(10).counts[0]["hour"]
  end

  def test_lru_forgets_the_oldest_key_beyond_the_dedup_size
    a = agg(dedup: 2)
    %w[u1 u2 u3 u1].each { |k| a.add(served("f", "on", k)) }
    assert_equal 4, a.pending[0]
  end

  def test_max_pending_drops_exposures_but_never_counts
    a = agg(pending: 2)
    5.times { |i| a.add(served("f", "on", "u#{i}")) }
    a.add(served("f", "on", "u0")) # a duplicate is not a drop
    assert_equal 2, a.pending[0]
    assert_equal 3, a.dropped
    assert_equal 6, a.take(1000).counts[0]["count"]
  end

  def test_take_returns_at_most_the_limit_of_each_list_oldest_first
    a = agg(dedup: 10_000, pending: 10_000)
    1500.times { |i| a.add(served("f#{i}", "on", "u")) }
    b = a.take(1000)
    assert_equal [1000, 1000], [b.exposures.size, b.counts.size]
    assert_equal "f0", b.exposures[0]["flag_key"]
    assert_equal "f999", b.counts[999]["flag_key"]
    b = a.take(1000)
    assert_equal [500, 500], [b.exposures.size, b.counts.size]
    assert_equal "f1000", b.exposures[0]["flag_key"]
    assert_equal "f1000", b.counts[0]["flag_key"]
    assert_equal [0, 0], a.pending
  end

  def test_take_splits_a_count_over_1e9_into_several_items
    a = agg(dedup: 10, pending: 10)
    a.add(served("f", "on", "u"))
    a.restore(Events::Batch.new([], [count("f", "on", "2026-11-10T09:00:00Z", 2_499_999_999)]))
    assert_empty a.take(0).counts
    assert_equal([1_000_000_000, 1_000_000_000], a.take(2).counts.map { |c| c["count"] })
    assert_equal([500_000_000], a.take(2).counts.map { |c| c["count"] })
    assert_equal [0, 0], a.pending
  end

  def test_restore_puts_a_batch_in_front_and_merges_its_counts
    a = agg
    a.add(served("f", "on", "u1"))
    a.add(served("f", "on", "u2"))
    a.add(unserved("f"))
    sent = a.take(1000)

    a.add(served("f", "on", "u3", T0 + 5))
    a.add(served("g", "on", "u3", T0 + 6))
    a.restore(sent)

    b = a.take(1000)
    assert_equal([["f", sent.exposures[0]["timestamp"]], ["f", sent.exposures[1]["timestamp"]],
                  ["f", "2026-11-10T09:15:02.005Z"], ["g", "2026-11-10T09:15:02.006Z"]],
                 b.exposures.map { |e| [e["flag_key"], e["timestamp"]] })
    assert_equal [count("f", "on", "2026-11-10T09:00:00Z", 3), count("f", nil, "2026-11-10T09:00:00Z", 1),
                  count("g", "on", "2026-11-10T09:00:00Z", 1)], b.counts
  end

  def test_restore_joins_the_items_of_a_split_count
    a = agg(dedup: 10, pending: 10)
    a.restore(Events::Batch.new([], [count("f", "on", "2026-11-10T09:00:00Z", 1_000_000_000),
                                     count("f", "on", "2026-11-10T09:00:00Z", 5)]))
    assert_equal [0, 1], a.pending
    assert_equal([1_000_000_000, 5], a.take(10).counts.map { |c| c["count"] })
  end

  def test_batch_serialises_to_the_body_of_post_sdk_v1_events
    a = agg(dedup: 10, pending: 10, send_context: true)
    a.add(served("new-checkout", "on", "user-1"))
    a.add(unserved("new-checkout"))
    assert_equal({ "exposures" => [{ "flag_key" => "new-checkout", "variant" => "on", "reason" => "SPLIT",
                                     "timestamp" => "2026-11-10T09:15:02.000Z",
                                     "context" => { "key" => "user-1" } }],
                   "counts" => [count("new-checkout", "on", "2026-11-10T09:00:00Z", 1),
                                count("new-checkout", nil, "2026-11-10T09:00:00Z", 1)] },
                 JSON.parse(a.take(10).body))
    assert_equal '{"exposures":[],"counts":[]}', agg.take(10).body
  end

  def test_body_is_utf8_without_ascii_escapes
    a = agg(send_context: true)
    a.add(Events::Evaluation.new(flag_key: "f", variant: "é", served: true, reason: "STATIC", context_key: "u",
                                 context: { "name" => "Zoë" }, at: T0))
    body = a.take(10).body
    assert_equal Encoding::BINARY, body.encoding
    assert_includes body, "Zoë".b
    refute_includes body, "\\u"
  end

  def test_body_refuses_a_context_that_is_not_json
    a = agg(send_context: true)
    a.add(Events::Evaluation.new(flag_key: "f", variant: "on", served: true, reason: "STATIC", context_key: "u",
                                 context: { "x" => Float::NAN }, at: T0))
    assert_raises(JSON::GeneratorError) { a.take(10).body }
  end
end
