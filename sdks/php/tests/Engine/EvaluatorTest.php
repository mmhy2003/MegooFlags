<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Engine;

use InvalidArgumentException;
use MegooFlags\Internal\Engine\Bucketing;
use MegooFlags\Internal\Engine\Compiler;
use MegooFlags\Internal\Engine\Evaluator;
use MegooFlags\Internal\Engine\Result;
use MegooFlags\Tests\Support\RulesetGen as G;
use PHPUnit\Framework\TestCase;
use stdClass;

final class EvaluatorTest extends TestCase
{
    /**
     * @param list<stdClass> $flags
     * @param array<string, stdClass> $segments
     * @return array<string, mixed>
     */
    private static function ruleset(array $flags, array $segments = []): array
    {
        return Compiler::compile(G::ruleset($flags, $segments));
    }

    /** @param array<string, mixed> $ruleset */
    private static function evaluate(array $ruleset, string $key, mixed $context = null, mixed $default = 'dflt', ?string $type = null): Result
    {
        return (new Evaluator(false))->evaluate($ruleset, $key, $context ?? new stdClass(), $default, $type);
    }

    private static function assertResult(Result $result, mixed $value, ?string $variant, string $reason, ?string $errorCode = null): void
    {
        self::assertSame($value, $result->value, 'value');
        self::assertSame($variant, $result->variant, 'variant');
        self::assertSame($reason, $result->reason, 'reason');
        self::assertSame($errorCode, $result->errorCode, 'error code');
    }

    /** @return array<string, mixed> a flag f serving on when the clause matches, else off */
    private static function oneRule(stdClass $clause, array $segments = []): array
    {
        return self::ruleset([G::flag('f', ['rules' => [G::rule('on', $clause)], 'fallthrough' => G::serve('off')])], $segments);
    }

    /**
     * @param array<string, mixed> $context
     * @param array<string, stdClass> $segments
     */
    private static function hits(stdClass $clause, array $context, array $segments = []): bool
    {
        return self::evaluate(self::oneRule($clause, $segments), 'f', (object) $context)->variant === 'on';
    }

    public function testDisabledServesTheOffVariation(): void
    {
        $rs = self::ruleset([G::flag('f', ['enabled' => false]), G::flag('g', ['enabled' => false, 'off_variation' => null])]);
        self::assertResult(self::evaluate($rs, 'f'), 'off', 'off', 'DISABLED');
        self::assertResult(self::evaluate($rs, 'g'), 'dflt', null, 'DISABLED');
    }

    public function testFlagNotFound(): void
    {
        $result = self::evaluate(self::ruleset([]), 'missing');
        self::assertResult($result, 'dflt', null, 'ERROR', 'FLAG_NOT_FOUND');
        self::assertStringContainsString('missing', (string) $result->errorMessage);
    }

    public function testInvalidContext(): void
    {
        $rs = self::ruleset([G::flag('f')]);
        foreach ([[], ['key' => 'a'], 'ctx', null, 1] as $context) {
            $result = (new Evaluator(false))->evaluate($rs, 'f', $context, 'dflt', null);
            self::assertResult($result, 'dflt', null, 'ERROR', 'INVALID_CONTEXT');
        }
    }

    public function testTargets(): void
    {
        $rs = self::ruleset([G::flag('f', ['targets' => [G::target('off', ['alice', '1'])]])]);
        self::assertResult(self::evaluate($rs, 'f', (object) ['key' => 'alice']), 'off', 'off', 'TARGETING_MATCH');
        self::assertResult(self::evaluate($rs, 'f', (object) ['key' => '1']), 'off', 'off', 'TARGETING_MATCH');
        self::assertResult(self::evaluate($rs, 'f', (object) ['key' => 1]), 'on', 'on', 'DEFAULT');
        self::assertResult(self::evaluate($rs, 'f', (object) ['key' => 'bob']), 'on', 'on', 'DEFAULT');
    }

    public function testFirstMatchingRuleAndItsIndex(): void
    {
        $rs = self::ruleset([G::flag('f', ['rules' => [
            G::rule('on', G::clause('plan', 'in', ['free'])),
            G::rule('off', G::clause('plan', 'in', ['pro'])),
        ]])]);
        $result = self::evaluate($rs, 'f', (object) ['plan' => 'pro']);
        self::assertResult($result, 'off', 'off', 'TARGETING_MATCH');
        self::assertSame(1, $result->ruleIndex);
        self::assertSame(['rule_index' => 1], $result->metadata());
        self::assertSame([], self::evaluate($rs, 'f', (object) ['plan' => 'x'])->metadata());
    }

    public function testAllClausesOfARuleMustMatch(): void
    {
        $rs = self::ruleset([G::flag('f', ['rules' => [G::rule('off', G::clause('a', 'in', [1]), G::clause('b', 'in', [2]))]])]);
        self::assertSame('off', self::evaluate($rs, 'f', (object) ['a' => 1, 'b' => 2])->variant);
        self::assertSame('on', self::evaluate($rs, 'f', (object) ['a' => 1, 'b' => 3])->variant);
    }

    public function testClauseNegate(): void
    {
        $negated = G::clause('plan', 'in', ['pro'], true);
        self::assertTrue(self::hits($negated, ['plan' => 'free']));
        self::assertFalse(self::hits($negated, ['plan' => 'pro']));
        self::assertFalse(self::hits($negated, []), 'a missing attribute is false without negate');
        self::assertFalse(self::hits($negated, ['plan' => null]), 'a null attribute is false without negate');
        self::assertTrue(self::hits($negated, ['plan' => (object) ['a' => 1]]), 'an object never matches, then negate');
        self::assertTrue(self::hits($negated, ['plan' => ['a' => 'pro']]), 'a non-list array is an object');
        self::assertTrue(self::hits($negated, ['plan' => []]), 'an empty array matches nothing, then negate');
    }

    public function testArraysMatchAnyElement(): void
    {
        self::assertTrue(self::hits(G::clause('tags', 'in', ['b']), ['tags' => ['a', 'b']]));
        self::assertFalse(self::hits(G::clause('tags', 'in', ['c']), ['tags' => ['a', 'b']]));
        self::assertFalse(self::hits(G::clause('tags', 'in', ['b']), ['tags' => [['b']]]));
        self::assertFalse(self::hits(G::clause('tags', 'in', ['b']), ['tags' => [1 => 'b']]), 'not a list');
    }

    public function testInAndNotIn(): void
    {
        self::assertTrue(self::hits(G::clause('n', 'in', [1]), ['n' => 1.0]));
        self::assertFalse(self::hits(G::clause('n', 'in', [1]), ['n' => true]));
        self::assertFalse(self::hits(G::clause('n', 'in', ['1']), ['n' => 1]));
        self::assertTrue(self::hits(G::clause('n', 'not_in', ['a']), ['n' => 'b']));
        self::assertFalse(self::hits(G::clause('n', 'not_in', ['a']), ['n' => 'a']));
        self::assertFalse(self::hits(G::clause('n', 'not_in', ['a']), ['n' => (object) ['x' => 1]]));
    }

    public function testStringOperators(): void
    {
        self::assertTrue(self::hits(G::clause('s', 'contains', ['ell']), ['s' => 'hello']));
        self::assertFalse(self::hits(G::clause('s', 'contains', ['1']), ['s' => 1]));
        self::assertTrue(self::hits(G::clause('s', 'starts_with', ['he']), ['s' => 'hello']));
        self::assertFalse(self::hits(G::clause('s', 'starts_with', ['lo']), ['s' => 'hello']));
        self::assertTrue(self::hits(G::clause('s', 'ends_with', ['lo']), ['s' => 'hello']));
        self::assertTrue(self::hits(G::clause('s', 'ends_with', ['é']), ['s' => 'café']));
        self::assertFalse(self::hits(G::clause('s', 'ends_with', ['he']), ['s' => 'hello']));
    }

    public function testMatches(): void
    {
        self::assertTrue(self::hits(G::clause('s', 'matches', ['^a.c$']), ['s' => 'abc']));
        self::assertFalse(self::hits(G::clause('s', 'matches', ['^a.c$']), ['s' => 'abcd']));
        self::assertFalse(self::hits(G::clause('s', 'matches', ['a']), ['s' => 1]));
        self::assertFalse(self::hits(G::clause('s', 'matches', ['(?=a)']), ['s' => 'a']));
        self::assertFalse(self::hits(G::clause('s', 'matches', ['a']), ['s' => "a\xff"]), 'invalid UTF-8');
    }

    public function testNumericOperators(): void
    {
        self::assertTrue(self::hits(G::clause('n', 'lt', [2]), ['n' => 1]));
        self::assertFalse(self::hits(G::clause('n', 'lt', [2]), ['n' => 2]));
        self::assertTrue(self::hits(G::clause('n', 'lte', [2]), ['n' => 2.0]));
        self::assertTrue(self::hits(G::clause('n', 'gt', [1.5]), ['n' => 2]));
        self::assertTrue(self::hits(G::clause('n', 'gte', [2]), ['n' => 2]));
        self::assertFalse(self::hits(G::clause('n', 'gt', [1]), ['n' => '2']));
        self::assertFalse(self::hits(G::clause('n', 'gt', [0]), ['n' => true]));
        self::assertTrue(self::hits(G::clause('n', 'gt', [9007199254740992]), ['n' => 9007199254740993]));
    }

    public function testSemverOperators(): void
    {
        self::assertTrue(self::hits(G::clause('v', 'semver_eq', ['1.2.3']), ['v' => '1.2.3+build']));
        self::assertTrue(self::hits(G::clause('v', 'semver_lt', ['1.0.0']), ['v' => '1.0.0-rc.1']));
        self::assertTrue(self::hits(G::clause('v', 'semver_gt', ['1.5.0']), ['v' => '1.10.0']));
        self::assertFalse(self::hits(G::clause('v', 'semver_gt', ['1.5.0']), ['v' => 'v2.0.0']));
        self::assertFalse(self::hits(G::clause('v', 'semver_eq', ['1.0.0']), ['v' => 1]));
    }

    public function testDateOperators(): void
    {
        self::assertTrue(self::hits(G::clause('t', 'before', ['2025-01-01T00:00:00Z']), ['t' => '2024-12-31T23:59:59Z']));
        self::assertTrue(self::hits(G::clause('t', 'before', ['1970-01-01T00:00:01Z']), ['t' => 999.9]));
        self::assertFalse(self::hits(G::clause('t', 'before', ['1970-01-01T00:00:01Z']), ['t' => 1000]));
        self::assertTrue(self::hits(G::clause('t', 'after', ['1970-01-01T00:00:00Z']), ['t' => '1970-01-01T01:00:00+00:30']));
        self::assertFalse(self::hits(G::clause('t', 'after', [0]), ['t' => 5]), 'a number clause value never matches');
        self::assertFalse(self::hits(G::clause('t', 'after', ['1970-01-01T00:00:00Z']), ['t' => 'garbage']));
    }

    /** @return array<string, stdClass> */
    private static function segments(): array
    {
        return ['s' => G::segment(['in', 'both'], ['out', 'both'], [[G::clause('org', 'in', ['acme'])]])];
    }

    public function testSegments(): void
    {
        $clause = G::clause('ignored', 'in_segment', ['s']);
        self::assertTrue(self::hits($clause, ['key' => 'in'], self::segments()));
        self::assertFalse(self::hits($clause, ['key' => 'both', 'org' => 'acme'], self::segments()), 'excluded wins');
        self::assertFalse(self::hits($clause, ['key' => 'out', 'org' => 'acme'], self::segments()));
        self::assertTrue(self::hits($clause, ['key' => 'other', 'org' => 'acme'], self::segments()));
        self::assertTrue(self::hits($clause, ['org' => 'acme'], self::segments()), 'no key: the rules decide');
        self::assertFalse(self::hits($clause, ['key' => 'other'], self::segments()));
        self::assertFalse(self::hits(G::clause('k', 'in_segment', ['missing']), ['key' => 'in'], self::segments()));
        self::assertTrue(self::hits(G::clause('k', 'in_segment', ['missing', 's']), ['key' => 'in'], self::segments()));
        self::assertTrue(self::hits(G::clause('k', 'in_segment', ['s'], true), ['key' => 'out'], self::segments()));
    }

    private static function halves(string $bucketBy): stdClass
    {
        return G::rollout($bucketBy, [['on', 50000], ['off', 50000]]);
    }

    public function testRollout(): void
    {
        $rs = self::ruleset([G::flag('f', ['fallthrough' => self::halves('org')])]);
        self::assertSame('SPLIT', self::evaluate($rs, 'f')->reason);
        self::assertSame('on', self::evaluate($rs, 'f')->variant, 'a missing value is bucket 0');
        self::assertSame('on', self::evaluate($rs, 'f', (object) ['org' => 1.5])->variant, 'a value that cannot be bucketed is bucket 0');
        $expected = Bucketing::bucket('f', 'acme') < 50000 ? 'on' : 'off';
        self::assertSame($expected, self::evaluate($rs, 'f', (object) ['key' => 'x', 'org' => 'acme'])->variant);
        $expected = Bucketing::bucket('f', '42') < 50000 ? 'on' : 'off';
        self::assertSame($expected, self::evaluate($rs, 'f', (object) ['org' => 42.0])->variant);
    }

    public function testRolloutInARuleIsTargetingMatch(): void
    {
        $rule = (object) ['clauses' => [G::clause('a', 'in', [1])], 'serve' => self::halves('key')];
        $rs = self::ruleset([G::flag('f', ['rules' => [$rule]])]);
        $result = self::evaluate($rs, 'f', (object) ['a' => 1]);
        self::assertSame('TARGETING_MATCH', $result->reason);
        self::assertSame(0, $result->ruleIndex);
    }

    public function testPrerequisiteFailed(): void
    {
        $rs = self::ruleset([
            G::flag('p', ['fallthrough' => G::serve('off')]),
            G::flag('f', ['prerequisites' => [G::prerequisite('p', 'on')]]),
        ]);
        $result = self::evaluate($rs, 'f');
        self::assertResult($result, 'off', 'off', 'PREREQUISITE_FAILED');
        self::assertSame('p', $result->prerequisite);
        self::assertSame(['prerequisite' => 'p'], $result->metadata());
    }

    public function testPrerequisiteMissingOrDisabledFails(): void
    {
        $rs = self::ruleset([
            G::flag('d', ['enabled' => false, 'off_variation' => 'on']),
            G::flag('f', ['prerequisites' => [G::prerequisite('missing', 'on')]]),
            G::flag('g', ['prerequisites' => [G::prerequisite('d', 'on')]]),
        ]);
        self::assertSame('PREREQUISITE_FAILED', self::evaluate($rs, 'f')->reason);
        self::assertSame('PREREQUISITE_FAILED', self::evaluate($rs, 'g')->reason);
    }

    public function testPrerequisiteMet(): void
    {
        $rs = self::ruleset([G::flag('p'), G::flag('f', ['prerequisites' => [G::prerequisite('p', 'on')]])]);
        self::assertResult(self::evaluate($rs, 'f'), 'on', 'on', 'DEFAULT');
    }

    public function testPrerequisiteServingItsOffVariationCanPass(): void
    {
        // p's prerequisite fails, so p serves its off variation "on", which f requires.
        $rs = self::ruleset([
            G::flag('q', ['fallthrough' => G::serve('off')]),
            G::flag('p', ['off_variation' => 'on', 'prerequisites' => [G::prerequisite('q', 'on')]]),
            G::flag('f', ['prerequisites' => [G::prerequisite('p', 'on')]]),
        ]);
        self::assertSame('PREREQUISITE_FAILED', self::evaluate($rs, 'p')->reason);
        self::assertResult(self::evaluate($rs, 'f'), 'on', 'on', 'DEFAULT');
    }

    /** @return array<string, mixed> flags c0..c<n> where each c<i> requires c<i+1> to serve on */
    private static function chain(int $length): array
    {
        $flags = [];
        for ($i = 0; $i < $length; $i++) {
            $flags[] = G::flag("c{$i}", ['prerequisites' => [G::prerequisite('c' . ($i + 1), 'on')]]);
        }
        $flags[] = G::flag("c{$length}");

        return self::ruleset($flags);
    }

    public function testPrerequisiteDepthGuard(): void
    {
        self::assertResult(self::evaluate(self::chain(32), 'c0'), 'on', 'on', 'DEFAULT');
        self::assertResult(self::evaluate(self::chain(33), 'c0'), 'dflt', null, 'ERROR', 'GENERAL');
    }

    public function testPrerequisiteCycleIsGeneral(): void
    {
        $rs = self::ruleset([
            G::flag('a', ['prerequisites' => [G::prerequisite('b', 'on')]]),
            G::flag('b', ['prerequisites' => [G::prerequisite('a', 'on')]]),
        ]);
        self::assertResult(self::evaluate($rs, 'a'), 'dflt', null, 'ERROR', 'GENERAL');
    }

    public function testStaticAndDefaultReasons(): void
    {
        $rs = self::ruleset([G::flag('s'), G::flag('t', ['targets' => [G::target('off', ['x'])]])]);
        self::assertResult(self::evaluate($rs, 's'), 'on', 'on', 'STATIC');
        self::assertResult(self::evaluate($rs, 't'), 'on', 'on', 'DEFAULT');
    }

    public function testTypeMismatch(): void
    {
        $rs = self::ruleset([
            G::flag('s'),
            G::flag('b', ['type' => 'boolean', 'variations' => [G::variation('on', true)], 'off_variation' => null]),
            G::flag('n', ['type' => 'number', 'variations' => [G::variation('on', 1.5)], 'off_variation' => null]),
            G::flag('d', ['enabled' => false, 'off_variation' => null]),
        ]);
        self::assertResult(self::evaluate($rs, 's', null, 'dflt', 'boolean'), 'dflt', null, 'ERROR', 'TYPE_MISMATCH');
        self::assertResult(self::evaluate($rs, 's', null, 'dflt', 'string'), 'on', 'on', 'STATIC');
        self::assertResult(self::evaluate($rs, 'b', null, 'dflt', 'boolean'), true, 'on', 'STATIC');
        self::assertResult(self::evaluate($rs, 'b', null, 'dflt', 'number'), 'dflt', null, 'ERROR', 'TYPE_MISMATCH');
        self::assertResult(self::evaluate($rs, 'n', null, 'dflt', 'number'), 1.5, 'on', 'STATIC');
        self::assertResult(self::evaluate($rs, 'n', null, 'dflt', 'json'), 1.5, 'on', 'STATIC');
        self::assertResult(self::evaluate($rs, 'd', null, 'dflt', 'boolean'), 'dflt', null, 'DISABLED');
    }

    public function testNullVariationValueServedForJson(): void
    {
        $rs = self::ruleset([G::flag('j', [
            'type' => 'json', 'variations' => [G::variation('nothing', null)],
            'off_variation' => null, 'fallthrough' => G::serve('nothing'),
        ])]);
        self::assertResult(self::evaluate($rs, 'j', null, 'dflt', 'json'), null, 'nothing', 'STATIC');
    }

    public function testCompositeValuesAreDecodedAsAskedAndFreshEachTime(): void
    {
        $rs = self::ruleset([G::flag('j', [
            'type' => 'json', 'variations' => [G::variation('on', (object) ['a' => [1, (object) []]])],
            'off_variation' => null,
        ])]);
        $asObjects = (new Evaluator(false))->evaluate($rs, 'j', new stdClass(), null, 'json')->value;
        self::assertEquals((object) ['a' => [1, new stdClass()]], $asObjects);
        $asArrays = (new Evaluator(true))->evaluate($rs, 'j', new stdClass(), null, 'json')->value;
        self::assertSame(['a' => [1, []]], $asArrays);

        self::assertInstanceOf(stdClass::class, $asObjects);
        $asObjects->a = 'changed';
        self::assertEquals((object) ['a' => [1, new stdClass()]], (new Evaluator(false))->evaluate($rs, 'j', new stdClass(), null, null)->value);
    }

    public function testCompositeValueIsATypeMismatchForScalarTypes(): void
    {
        $rs = self::ruleset([G::flag('j', ['type' => 'json', 'variations' => [G::variation('on', [1])], 'off_variation' => null])]);
        self::assertResult(self::evaluate($rs, 'j', null, 'dflt', 'string'), 'dflt', null, 'ERROR', 'TYPE_MISMATCH');
    }

    public function testUnknownExpectedTypeThrows(): void
    {
        $this->expectException(InvalidArgumentException::class);
        self::evaluate(self::ruleset([G::flag('s')]), 's', null, 'dflt', 'integer');
    }
}
