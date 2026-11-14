# frozen_string_literal: true

require_relative "test_helper"
require "megooflags"

class VersionTest < Minitest::Test
  def test_version
    assert_equal "0.1.0", MegooFlags::VERSION
  end

  def test_gemspec_loads_with_the_version
    spec = Gem::Specification.load(File.expand_path("../megooflags.gemspec", __dir__))

    refute_nil spec
    assert_equal "megooflags", spec.name
    assert_equal Gem::Version.new(MegooFlags::VERSION), spec.version
  end
end
