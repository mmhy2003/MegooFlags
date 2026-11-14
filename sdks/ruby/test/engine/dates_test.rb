# frozen_string_literal: true

require_relative "../test_helper"
require "megooflags/internal/engine/dates"

class DatesTest < Minitest::Test
  Dates = MegooFlags::Internal::Engine::Dates

  VALID = {
    "1970-01-01T00:00:00Z" => 0,
    "1970-01-01T00:00:00.123456789Z" => 123,
    "1970-01-01T00:00:00.5Z" => 500,
    "1970-01-01T01:00:00+01:00" => 0,
    "1969-12-31T23:59:59Z" => -1000,
    "2026-01-01t00:00:00z" => 1_767_225_600_000,
    "2026-01-01 00:00:00Z" => 1_767_225_600_000,
    "2025-12-31T19:00:00-05:00" => 1_767_225_600_000,
    "2024-02-29T00:00:00Z" => 1_709_164_800_000,
    "2026-01-01T00:00:00+23:59" => 1_767_139_260_000,
    "0001-01-01T00:00:00Z" => -62_135_596_800_000,
    "9999-12-31T23:59:59.999Z" => 253_402_300_799_999
  }.freeze

  INVALID = [
    "2026-01-01", "2026-02-30T00:00:00Z", "2025-02-29T00:00:00Z", "1900-02-29T00:00:00Z",
    "2026-04-31T00:00:00Z", "2026-13-01T00:00:00Z", "2026-00-01T00:00:00Z", "2026-01-00T00:00:00Z",
    "2026-01-01T24:00:00Z", "2026-01-01T00:60:00Z", "2026-01-01T00:00:60Z", "2026-01-01T00:00:00",
    "2026-01-01T00:00:00+24:00", "2026-01-01T00:00:00+0100", "0000-01-01T00:00:00Z",
    "2026-01-01T00:00:00.Z", "2026-01-01T00:00:00Z\n", "\n2026-01-01T00:00:00Z", " 2026-01-01T00:00:00Z",
    "2026-01-01X00:00:00Z", "٢026-01-01T00:00:00Z", "2026-01-01T00:00:00.١Z",
    "26-01-01T00:00:00Z", ""
  ].freeze

  def test_valid
    VALID.each { |s, ms| assert_equal ms, Dates.parse_rfc3339_millis(s), s }
  end

  def test_invalid
    INVALID.each { |s| assert_nil Dates.parse_rfc3339_millis(s), s.inspect }
  end

  def test_to_millis
    assert_equal 1500, Dates.to_millis(1500.9)
    assert_equal(-1, Dates.to_millis(-0.5))
    assert_equal 1500, Dates.to_millis(1500)
    assert_equal 0, Dates.to_millis("1970-01-01T00:00:00Z")
    assert_kind_of Integer, Dates.to_millis(1500.9)
  end

  def test_to_millis_invalid
    ["nope", Float::NAN, Float::INFINITY, -Float::INFINITY, true, false, nil, []].each do |v|
      assert_nil Dates.to_millis(v), v.inspect
    end
  end
end
