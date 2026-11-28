<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Support;

use stdClass;

/**
 * Ruleset builders for the engine tests and the p99 guard (the Go SDK's
 * genRuleset). Everything is a JSON tree as json_decode($text, false) gives
 * it: objects are stdClass, arrays are lists.
 */
final class RulesetGen
{
    public const BENCH_SEGMENTS = 50;

    private function __construct() {}

    /**
     * flag is an enabled string flag with variations on and off, off_variation
     * off and fallthrough on; $fields override any of it.
     *
     * @param array<string, mixed> $fields
     */
    public static function flag(string $key, array $fields = []): stdClass
    {
        return (object) array_merge([
            'key' => $key, 'type' => 'string', 'enabled' => true, 'version' => 1,
            'variations' => [self::variation('on', 'on'), self::variation('off', 'off')],
            'off_variation' => 'off', 'prerequisites' => [], 'targets' => [], 'rules' => [],
            'fallthrough' => self::serve('on'),
        ], $fields);
    }

    public static function variation(string $key, mixed $value): stdClass
    {
        return (object) ['key' => $key, 'value' => $value];
    }

    public static function serve(string $variation): stdClass
    {
        return (object) ['variation' => $variation];
    }

    /** @param list<array{string, int}> $weights [variation, weight] pairs */
    public static function rollout(string $bucketBy, array $weights): stdClass
    {
        return (object) ['rollout' => (object) [
            'bucket_by' => $bucketBy,
            'weights' => array_map(
                static fn(array $w): stdClass => (object) ['variation' => $w[0], 'weight' => $w[1]],
                $weights,
            ),
        ]];
    }

    /** @param list<string> $values */
    public static function target(string $variation, array $values): stdClass
    {
        return (object) ['variation' => $variation, 'values' => $values];
    }

    public static function prerequisite(string $flag, string $variation): stdClass
    {
        return (object) ['flag' => $flag, 'variation' => $variation];
    }

    /** @param list<mixed> $values */
    public static function clause(string $attribute, string $operator, array $values, bool $negate = false): stdClass
    {
        return (object) ['attribute' => $attribute, 'operator' => $operator, 'negate' => $negate, 'values' => $values];
    }

    public static function rule(string $variation, stdClass ...$clauses): stdClass
    {
        return (object) ['description' => '', 'clauses' => array_values($clauses), 'serve' => self::serve($variation)];
    }

    /** @param list<string> $included @param list<string> $excluded @param list<list<stdClass>> $rules */
    public static function segment(array $included, array $excluded = [], array $rules = []): stdClass
    {
        return (object) [
            'included' => $included, 'excluded' => $excluded,
            'rules' => array_map(static fn(array $clauses): stdClass => (object) ['clauses' => $clauses], $rules),
        ];
    }

    /**
     * @param list<stdClass> $flags
     * @param array<string, stdClass> $segments
     */
    public static function ruleset(array $flags, array $segments = []): stdClass
    {
        $byKey = new stdClass();
        foreach ($flags as $flag) {
            $byKey->{$flag->key} = $flag;
        }

        return (object) [
            'schema_version' => 1, 'project' => 'p', 'environment' => 'e',
            'flags' => $byKey, 'segments' => (object) $segments,
        ];
    }

    /** @return list<string> */
    public static function flagKeys(int $count): array
    {
        return array_map(static fn(int $i): string => "flag-{$i}", range(0, $count - 1));
    }

    /**
     * genRuleset is a deterministic ruleset of $count flags and 50 segments.
     * Even flags are booleans with three rules (in, matches, semver_gt), a
     * 100-key target list and a three-way fallthrough rollout; odd flags are
     * strings with an in_segment rule and a before rule. Every 10th flag has a
     * prerequisite on the flag before it.
     */
    public static function genRuleset(int $count): stdClass
    {
        $flags = [];
        foreach (self::flagKeys($count) as $i => $key) {
            $flag = $i % 2 === 0 ? self::benchEven($key, $i) : self::benchOdd($key, $i);
            if ($i > 0 && $i % 10 === 0) {
                $flag->prerequisites = [self::prerequisite('flag-' . ($i - 1), 'on')];
            }
            $flags[] = $flag;
        }
        $segments = [];
        for ($s = 0; $s < self::BENCH_SEGMENTS; $s++) {
            $included = array_map(static fn(int $k): string => 'user-' . ($s * 1000 + $k), range(0, 999));
            $segments["segment-{$s}"] = self::segment($included, [], [[self::clause('org', 'in', ["org-{$s}"])]]);
        }
        $ruleset = self::ruleset($flags, $segments);
        $ruleset->project = 'bench';
        $ruleset->environment = 'bench';

        return $ruleset;
    }

    /**
     * benchContexts are 100 contexts that hit every rule shape of genRuleset.
     *
     * @return list<stdClass>
     */
    public static function benchContexts(): array
    {
        return array_map(static fn(int $j): stdClass => (object) [
            'key' => 'user-' . ($j * 503),
            'plan' => 'plan-' . ($j % 40),
            'email' => $j % 3 === 0 ? "user-{$j}" : "user{$j}@example.com",
            'app_version' => '1.' . ($j % 10) . '.0',
            'org' => 'org-' . ($j % 100),
        ], range(0, 99));
    }

    private static function benchEven(string $key, int $i): stdClass
    {
        return self::flag($key, [
            'type' => 'boolean',
            'variations' => [self::variation('on', true), self::variation('off', false), self::variation('alt', false)],
            'targets' => [self::target('alt', array_map(
                static fn(int $k): string => 'user-' . (($i + $k * 37) % 1000),
                range(0, 99),
            ))],
            'rules' => [
                self::rule('on', self::clause('plan', 'in', array_map(static fn(int $p): string => "plan-{$p}", range(0, 19)))),
                self::rule('off', self::clause('email', 'matches', ['^user-[0-9]+$'])),
                self::rule('alt', self::clause('app_version', 'semver_gt', ['1.5.0'])),
            ],
            'fallthrough' => self::rollout('key', [['on', 33334], ['off', 33333], ['alt', 33333]]),
        ]);
    }

    private static function benchOdd(string $key, int $i): stdClass
    {
        return self::flag($key, [
            'variations' => [self::variation('on', 'on'), self::variation('off', 'off'), self::variation('alt', 'alt')],
            'rules' => [
                self::rule('alt', self::clause('key', 'in_segment', ['segment-' . ($i % self::BENCH_SEGMENTS)])),
                self::rule('off', self::clause('signed_up', 'before', ['2025-01-01T00:00:00Z'])),
            ],
        ]);
    }
}
