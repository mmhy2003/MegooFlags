# frozen_string_literal: true

require_relative "../test_helper"
require "megooflags/internal/delivery/store"

class StoreTest < Minitest::Test
  Delivery = MegooFlags::Internal::Delivery

  def snapshot(etag, source) = Delivery::Snapshot.new(ruleset: Object.new, etag: etag, source: source)

  def test_load_before_swap_is_nil
    assert_nil Delivery::Store.new.load
  end

  def test_swap_installs_the_snapshot_and_returns_the_old_one
    store = Delivery::Store.new
    first = snapshot('"a"', "bootstrap")
    second = snapshot('"b"', "stream")

    assert_nil store.swap(first)
    assert_same first, store.load
    assert_same first, store.swap(second)
    assert_same second, store.load
  end

  def test_snapshot_is_frozen
    snap = snapshot("x", "poll")

    assert_predicate snap, :frozen?
    assert_predicate snap.etag, :frozen?
  end

  def test_snapshot_refuses_an_unknown_source
    assert_raises(ArgumentError) { snapshot("x", "elsewhere") }
  end
end
