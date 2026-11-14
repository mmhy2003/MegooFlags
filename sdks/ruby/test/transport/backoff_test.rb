# frozen_string_literal: true

require_relative "../test_helper"
require "megooflags/internal/transport/backoff"

class BackoffTest < Minitest::Test
  Backoff = MegooFlags::Internal::Transport::Backoff

  def waits(backoff, count) = Array.new(count) { backoff.next }

  def test_doubles_from_base_up_to_max
    assert_equal [3, 6, 12, 24, 48, 60, 60], waits(Backoff.new(3, 60, rand: -> { 1.0 }), 7)
  end

  def test_jitters_from_the_second_wait
    got = waits(Backoff.new(3, 60, rand: -> { 0.0 }), 4)

    assert_equal 3, got[0]
    [4.8, 9.6, 19.2].zip(got.drop(1)).each { |want, wait| assert_in_delta want, wait }
  end

  def test_first_wait_is_never_above_max
    assert_equal 2, Backoff.new(5, 2, rand: -> { 1.0 }).next
  end

  def test_never_goes_above_max
    b = Backoff.new(0.5, 7)

    200.times { assert_operator b.next, :<=, 7 }
  end

  def test_reset_starts_over
    b = Backoff.new(3, 60, rand: -> { 1.0 })
    waits(b, 3)
    b.reset

    assert_equal [3, 6], waits(b, 2)
  end

  def test_base_can_be_replaced
    b = Backoff.new(3, 60, rand: -> { 1.0 })
    b.base = 1

    assert_equal [1, 2, 4], waits(b, 3)
  end

  def test_default_random_source_stays_in_the_jitter_range
    b = Backoff.new(10, 1000)
    b.next
    wait = b.next

    assert_operator wait, :>=, 16
    assert_operator wait, :<=, 20
  end
end
