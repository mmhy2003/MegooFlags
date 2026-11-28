# frozen_string_literal: true

# p99 guard: an evaluation stays under 1 ms on 10 000 flags. It runs alone,
# with `bundle exec rake p99`, so other tests don't compete for the CPU.

require "json"
require_relative "../test/test_helper"
require_relative "../test/support/ruleset_gen"
require "megooflags/internal/engine/parser"
require "megooflags/internal/engine/evaluator"

# Times evaluations one by one on the generated 10 000-flag ruleset.
class P99Test < Minitest::Test
  Engine = MegooFlags::Internal::Engine
  FLAGS = 10_000
  WARM_UP = 10_000
  SAMPLES = 100_000

  def test_p99_under_1ms
    ruleset = Engine::Parser.parse(JSON.generate(RulesetGen.gen_ruleset(FLAGS)))
    keys = RulesetGen.flag_keys(FLAGS)
    contexts = RulesetGen.bench_contexts
    rng = Random.new(12_345)
    pick = -> { [keys[rng.rand(FLAGS)], contexts[rng.rand(contexts.size)]] }

    WARM_UP.times do
      key, context = pick.call
      Engine::Evaluator.evaluate(ruleset, key, context, nil, nil)
    end
    samples = Array.new(SAMPLES) do
      key, context = pick.call
      start = Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond)
      Engine::Evaluator.evaluate(ruleset, key, context, nil, nil)
      (Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond) - start) / 1e6
    end
    samples.sort!
    p50 = samples[SAMPLES / 2]
    p99 = samples[SAMPLES * 99 / 100]
    puts format("p50 %<p50>.4f ms, p99 %<p99>.4f ms, max %<max>.4f ms", p50: p50, p99: p99, max: samples[-1])
    assert_operator p99, :<, 1.0
  end
end
