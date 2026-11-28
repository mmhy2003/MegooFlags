# frozen_string_literal: true

require_relative "../test_helper"
require "megooflags/internal/engine/bucketing"

class BucketingTest < Minitest::Test
  Bucketing = MegooFlags::Internal::Engine::Bucketing

  def test_bucket_text
    assert_equal "u", Bucketing.bucket_text("u")
    assert_equal "42", Bucketing.bucket_text(42)
    assert_equal "42", Bucketing.bucket_text(42.0)
    assert_equal "0", Bucketing.bucket_text(-0.0)
    assert_equal "-7", Bucketing.bucket_text(-7)
    assert_equal "9007199254740991", Bucketing.bucket_text(9_007_199_254_740_991)
    assert_equal "-9007199254740991", Bucketing.bucket_text(-9_007_199_254_740_991.0)
  end

  def test_bucket_text_refused
    [42.5, true, false, nil, [1], { "a" => 1 }, 9_007_199_254_740_992, 9_007_199_254_740_992.0,
     -9_007_199_254_740_992, Float::NAN, Float::INFINITY].each do |v|
      assert_nil Bucketing.bucket_text(v), v.inspect
    end
  end

  def test_bucket
    assert_equal 68_258, Bucketing.bucket("f", "u")
  end

  def test_pick_variation
    weights = [["a", 30_000], ["b", 40_000], ["c", 30_000]]

    assert_equal "a", Bucketing.pick_variation(weights, 29_999)
    assert_equal "b", Bucketing.pick_variation(weights, 30_000)
    assert_equal "b", Bucketing.pick_variation(weights, 68_258)
    assert_equal "c", Bucketing.pick_variation(weights, 99_999)
    assert_equal "b", Bucketing.pick_variation([["a", 0], ["b", 100_000]], 0)
  end
end
