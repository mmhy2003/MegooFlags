# frozen_string_literal: true

require_relative "../test_helper"
require "megooflags/internal/engine/values"

class ValuesTest < Minitest::Test
  Values = MegooFlags::Internal::Engine::Values

  def test_number
    assert Values.number?(1)
    assert Values.number?(1.5)
    refute Values.number?(true)
    refute Values.number?(false)
    refute Values.number?("1")
    refute Values.number?(nil)
  end

  def test_scalar
    assert Values.scalar?("a")
    assert Values.scalar?(true)
    assert Values.scalar?(false)
    assert Values.scalar?(1)
    assert Values.scalar?(1.5)
    refute Values.scalar?(nil)
    refute Values.scalar?([1])
    refute Values.scalar?({ "a" => 1 })
  end

  def test_json_equal
    assert Values.json_equal?(1, 1.0)
    refute Values.json_equal?(true, 1)
    refute Values.json_equal?(1, true)
    refute Values.json_equal?("1", 1)
    refute Values.json_equal?(nil, nil)
    assert Values.json_equal?(false, false)
    refute Values.json_equal?(true, false)
    assert Values.json_equal?("a", "a")
    refute Values.json_equal?([1], [1])
  end

  def test_json_equal_integers_compare_exactly
    refute Values.json_equal?(9_007_199_254_740_993, 9_007_199_254_740_992)
    assert Values.json_equal?(9_007_199_254_740_993, 9_007_199_254_740_993)
    assert Values.json_equal?(9_007_199_254_740_993, 9_007_199_254_740_992.0)
  end
end
