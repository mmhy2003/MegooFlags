# frozen_string_literal: true

$LOAD_PATH.unshift(File.expand_path("../lib", __dir__))

require "minitest/autorun"

# Helpers for the tests that run background threads.
module ThreadTestHelpers
  def monotonic = Process.clock_gettime(Process::CLOCK_MONOTONIC)

  # wait_for waits until the block is truthy, failing after timeout seconds.
  def wait_for(timeout = 5)
    deadline = monotonic + timeout
    until yield
      flunk "condition not met within #{timeout}s" if monotonic > deadline
      sleep 0.005
    end
  end

  # The thread Timeout starts on first use (Net::HTTP's open timeout) and keeps for the process.
  TIMEOUT_THREAD = "Timeout stdlib thread"

  # live_threads counts the live threads, without Timeout's.
  def live_threads = Thread.list.count { |t| t.name != TIMEOUT_THREAD }

  # assert_no_thread_leak checks that no thread started since setup (before = live_threads) is
  # still alive.
  def assert_no_thread_leak(before, timeout = 2)
    deadline = monotonic + timeout
    sleep 0.01 while live_threads > before && monotonic < deadline
    assert_equal before, live_threads, "live threads: #{Thread.list.map(&:name).inspect}"
  end
end

# TestLogger records messages by level.
class TestLogger
  def initialize
    @lock = Mutex.new
    @lines = []
  end

  %i[debug info warn error].each do |level|
    define_method(level) do |message = nil, &block|
      message = block.call if message.nil? && block
      @lock.synchronize { @lines << [level, message.to_s] }
      nil
    end
  end

  # messages lists the messages logged at level.
  def messages(level) = @lock.synchronize { @lines.select { |l, _| l == level }.map(&:last) }
end
