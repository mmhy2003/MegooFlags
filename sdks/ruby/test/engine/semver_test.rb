# frozen_string_literal: true

require_relative "../test_helper"
require "megooflags/internal/engine/semver"

class SemverTest < Minitest::Test
  Semver = MegooFlags::Internal::Engine::Semver

  VALID = %w[1.2.3 1.0.0-alpha.1 1.0.0+build.5 1.0.0-0.3.7 1.0.0-x-y-z.-- 1.0.0-alpha+b.1 0.0.0].freeze

  INVALID = [
    "v1.2.3", "1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.03", "1.2.3-01", "1.2.3-", "1.2.3+",
    "1.2.3-a..b", "1.2.3-a.", "1.2.3+b.", "1.2.3+b..c", "1.2.3\n", " 1.2.3", "1.2.3 ",
    "١.2.3", "1.2.3-α", "", "1..3", "1.2.3-a+b+c"
  ].freeze

  def test_valid
    VALID.each { |s| refute_nil Semver.parse_semver(s), s }
  end

  def test_invalid
    INVALID.each { |s| assert_nil Semver.parse_semver(s), s.inspect }
  end

  def test_invalid_utf8_is_not_a_version
    ["\xFF", "1.2.3\xFF", "1.2.3-\xFF", "1.2.3+\xFF", "\xC3"].each do |s|
      assert_nil Semver.parse_semver((+s).force_encoding(Encoding::UTF_8)), s.inspect
    end
  end

  def test_other_encodings_are_transcoded
    refute_nil Semver.parse_semver("1.2.3-rc.1".encode(Encoding::UTF_16LE))
    assert_nil Semver.parse_semver((+"1.2.3\xFF").force_encoding(Encoding::BINARY))
  end

  def test_parts_are_digit_strings
    v = Semver.parse_semver("1.20.300-rc.1+b")

    assert_equal %w[1 20 300], v.core
    pre = v.pre.map { |id| [id.numeric, id.text] }

    assert_equal [[false, "rc"], [true, "1"]], pre
    assert_nil Semver.parse_semver("1.2.3").pre
  end

  def test_order
    order = %w[1.0.0-alpha 1.0.0-alpha.1 1.0.0-alpha.beta 1.0.0-beta 1.0.0-beta.2 1.0.0-beta.11
               1.0.0-rc.1 1.0.0]
    order.each_cons(2) do |lo, hi|
      assert_equal(-1, compare(lo, hi), "#{lo} < #{hi}")
      assert_equal 1, compare(hi, lo), "#{hi} > #{lo}"
    end
  end

  def test_build_is_ignored
    assert_equal 0, compare("1.0.0+b1", "1.0.0+b2")
  end

  def test_core_compares_numerically
    assert_equal 1, compare("99999999999999999999.0.0", "9.0.0")
    assert_equal(-1, compare("1.9.0", "1.10.0"))
    assert_equal 0, compare("1.2.3", "1.2.3")
  end

  private

  def compare(left, right)
    Semver.compare_semver(Semver.parse_semver(left), Semver.parse_semver(right))
  end
end
