<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Perf;

use MegooFlags\Internal\Engine\Compiler;
use MegooFlags\Internal\Engine\Evaluator;
use MegooFlags\Tests\Support\RulesetGen;
use PHPUnit\Framework\TestCase;

/**
 * p99 guard: an evaluation stays under 1 ms on 10 000 flags. It runs alone,
 * with `composer p99`, so other tests don't compete for the CPU.
 */
final class P99Test extends TestCase
{
    private const FLAGS = 10000;
    private const WARM_UP = 10000;
    private const SAMPLES = 100000;

    public function testP99Under1Ms(): void
    {
        // The 10 000-flag tree, its JSON text and the compiled ruleset don't
        // fit the CLI's default 128 MB at the same time.
        ini_set('memory_limit', '1G');
        $ruleset = Compiler::compileJson((string) json_encode(RulesetGen::genRuleset(self::FLAGS)));
        $keys = RulesetGen::flagKeys(self::FLAGS);
        $contexts = RulesetGen::benchContexts();
        $evaluator = new Evaluator(true);
        mt_srand(12345);

        for ($i = 0; $i < self::WARM_UP; $i++) {
            $evaluator->evaluate($ruleset, $keys[mt_rand(0, self::FLAGS - 1)], $contexts[mt_rand(0, 99)], null, null);
        }
        $samples = [];
        for ($i = 0; $i < self::SAMPLES; $i++) {
            $key = $keys[mt_rand(0, self::FLAGS - 1)];
            $context = $contexts[mt_rand(0, 99)];
            $start = hrtime(true);
            $evaluator->evaluate($ruleset, $key, $context, null, null);
            $samples[] = (hrtime(true) - $start) / 1e6;
        }
        sort($samples);
        $p50 = $samples[intdiv(self::SAMPLES, 2)];
        $p99 = $samples[intdiv(self::SAMPLES * 99, 100)];
        fwrite(STDOUT, sprintf("\np50 %.4f ms, p99 %.4f ms, max %.4f ms\n", $p50, $p99, $samples[self::SAMPLES - 1]));
        self::assertLessThan(1.0, $p99);
    }
}
