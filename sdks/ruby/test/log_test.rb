# frozen_string_literal: true

require_relative "test_helper"
require "megooflags/internal/log"

class LogTest < Minitest::Test
  Log = MegooFlags::Internal::Log

  # Ruby 3.4 under Bundler prints a one-time notice that logger becomes a bundled gem in 4.0;
  # Ruby 4.0 under Bundler cannot load it at all, so the real default is the fallback there.
  def test_default_logger_is_a_warn_level_logger_or_the_fallback
    logger = nil
    capture_io { logger = Log.default_logger }

    if defined?(::Logger)
      assert_kind_of ::Logger, logger
      assert_equal ::Logger::WARN, logger.level
    else
      assert_instance_of Log::Stderr, logger
    end
  end

  def test_default_logger_falls_back_to_stderr_without_the_logger_gem
    Log.singleton_class.define_method(:require) { |_name| raise LoadError, "cannot load such file -- logger" }

    assert_instance_of Log::Stderr, Log.default_logger
  ensure
    Log.singleton_class.remove_method(:require)
  end

  def test_stderr_logger_writes_warnings_and_errors_only
    logger = Log::Stderr.new
    _, err = capture_io do
      logger.debug("d")
      logger.info("i")
      logger.warn("careful")
      logger.error("broken")
    end

    assert_equal "megooflags WARN: careful\nmegooflags ERROR: broken\n", err
  end

  def test_stderr_logger_accepts_a_block
    _, err = capture_io { Log::Stderr.new.warn { "lazy" } }

    assert_equal "megooflags WARN: lazy\n", err
  end
end
