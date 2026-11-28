<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Engine;

use MegooFlags\Internal\Engine\Compiler;
use MegooFlags\Internal\Engine\ParseException;
use MegooFlags\Internal\Engine\Regex;
use MegooFlags\Internal\Engine\Semver;
use MegooFlags\Tests\Support\RulesetGen as G;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use stdClass;

final class CompilerTest extends TestCase
{
    private static function validTree(): stdClass
    {
        $flag = G::flag('f', [
            'targets' => [G::target('on', ['alice', 'bob'])],
            'prerequisites' => [G::prerequisite('g', 'on')],
            'rules' => [(object) [
                'description' => 'pro',
                'clauses' => [G::clause('plan', 'in', ['pro'])],
                'serve' => G::serve('on'),
            ]],
            'fallthrough' => G::rollout('key', [['on', 40000], ['off', 60000]]),
        ]);
        $segments = ['s' => G::segment(['a'], ['b'], [[G::clause('org', 'in', ['o'])]])];

        return G::ruleset([$flag, G::flag('g')], $segments);
    }

    /**
     * errorFor compiles the valid tree after $change changed it and returns
     * the ParseException message.
     *
     * @param callable(stdClass): void $change
     */
    private static function errorFor(callable $change): string
    {
        $tree = self::validTree();
        $change($tree);
        try {
            Compiler::compile($tree);
        } catch (ParseException $e) {
            return $e->getMessage();
        }
        self::fail('no ParseException');
    }

    private static function clauseOf(stdClass $tree): stdClass
    {
        return $tree->flags->f->rules[0]->clauses[0];
    }

    public function testValidRuleset(): void
    {
        $rs = Compiler::compileJson((string) json_encode(self::validTree()));
        self::assertSame('p', $rs['project']);
        self::assertSame('e', $rs['environment']);
        self::assertSame([], $rs['warnings']);
        self::assertSame([
            'type' => 'string',
            'enabled' => true,
            'version' => 1,
            'variations' => ['on' => ['s' => 'on'], 'off' => ['s' => 'off']],
            'off' => 'off',
            'prereqs' => [['g', 'on']],
            'targets' => [['on', ['alice' => true, 'bob' => true]]],
            'rules' => [[
                'clauses' => [['a' => 'plan', 'op' => 'in', 'n' => false, 'p' => ['pro']]],
                'serve' => ['v' => 'on'],
            ]],
            'fallthrough' => ['by' => 'key', 'w' => [['on', 40000], ['off', 60000]]],
        ], $rs['flags']['f']);
        self::assertSame([
            'included' => ['a' => true],
            'excluded' => ['b' => true],
            'rules' => [[['a' => 'org', 'op' => 'in', 'n' => false, 'p' => ['o']]]],
        ], $rs['segments']['s']);
    }

    public function testTreeAndTextGiveTheSameRuleset(): void
    {
        self::assertSame(Compiler::compile(self::validTree()), Compiler::compileJson((string) json_encode(self::validTree())));
    }

    public function testVarExportRoundTripsAndHoldsNoObject(): void
    {
        $tree = self::validTree();
        $tree->flags->j = G::flag('j', [
            'type' => 'json', 'off_variation' => null, 'fallthrough' => G::serve('obj'),
            'variations' => [
                G::variation('obj', (object) ['a' => [1, (object) []], 'b' => null]),
                G::variation('list', []), G::variation('num', 1.5), G::variation('nil', null),
            ],
            'rules' => [
                G::rule('obj', G::clause('v', 'semver_gt', ['1.2.3-rc.1'])),
                G::rule('obj', G::clause('t', 'before', ['2025-01-01T00:00:00Z'])),
                G::rule('obj', G::clause('s', 'matches', ['^a$', '(?=x)'])),
            ],
        ]);
        $tree->flags->{'42'} = G::flag('42', ['targets' => [G::target('on', ['7', 'x'])]]);
        $compiled = Compiler::compile($tree);

        $path = tempnam(sys_get_temp_dir(), 'megooflags-compiled-');
        self::assertIsString($path);
        try {
            file_put_contents($path, '<?php return ' . var_export($compiled, true) . ";\n");
            $loaded = require $path;
        } finally {
            unlink($path);
        }
        self::assertSame($compiled, $loaded);
        self::assertSame(['j' => '{"a":[1,{}],"b":null}'], $compiled['flags']['j']['variations']['obj']);
        self::assertSame(['j' => '[]'], $compiled['flags']['j']['variations']['list']);
        self::assertSame(['s' => null], $compiled['flags']['j']['variations']['nil']);
        self::assertFalse(self::holdsObject($compiled));
    }

    private static function holdsObject(mixed $value): bool
    {
        if (is_object($value)) {
            return true;
        }
        if (is_array($value)) {
            foreach ($value as $item) {
                if (self::holdsObject($item)) {
                    return true;
                }
            }
        }

        return false;
    }

    public function testInvalidJson(): void
    {
        foreach (['{', 'NaN', "\"\xff\""] as $text) {
            try {
                Compiler::compileJson($text);
                self::fail("no ParseException for {$text}");
            } catch (ParseException $e) {
                self::assertStringStartsWith('ruleset: ', $e->getMessage());
            }
        }
    }

    /** @return iterable<string, array{string, callable(stdClass): void}> */
    public static function errors(): iterable
    {
        // Root
        yield 'schema_version missing' => ['schema_version', static function (stdClass $t): void {
            unset($t->schema_version);
        }];
        yield 'schema_version 2' => ['schema_version', static function (stdClass $t): void {
            $t->schema_version = 2;
        }];
        yield 'schema_version 1.0' => ['schema_version', static function (stdClass $t): void {
            $t->schema_version = 1.0;
        }];
        yield 'schema_version true' => ['schema_version', static function (stdClass $t): void {
            $t->schema_version = true;
        }];
        yield 'project' => ['project', static function (stdClass $t): void {
            $t->project = 1;
        }];
        yield 'environment' => ['environment', static function (stdClass $t): void {
            unset($t->environment);
        }];
        yield 'flags' => ['flags', static function (stdClass $t): void {
            $t->flags = [];
        }];
        yield 'segments' => ['segments', static function (stdClass $t): void {
            $t->segments = null;
        }];
        // Flag
        yield 'flag' => ['flags.f', static function (stdClass $t): void {
            $t->flags->f = [];
        }];
        yield 'flag key' => ['flags.f.key', static function (stdClass $t): void {
            $t->flags->f->key = 'x';
        }];
        yield 'flag type' => ['flags.f.type', static function (stdClass $t): void {
            $t->flags->f->type = 'int';
        }];
        yield 'variations empty' => ['flags.f.variations', static function (stdClass $t): void {
            $t->flags->f->variations = [];
        }];
        yield 'variation not an object' => ['flags.f.variations[0]', static function (stdClass $t): void {
            $t->flags->f->variations[0] = 'on';
        }];
        yield 'variation key' => ['flags.f.variations[0].key', static function (stdClass $t): void {
            $t->flags->f->variations[0]->key = 1;
        }];
        yield 'duplicate variation' => ['flags.f.variations[1].key', static function (stdClass $t): void {
            $t->flags->f->variations[1]->key = 'on';
        }];
        yield 'variation value' => ['flags.f.variations[0].value', static function (stdClass $t): void {
            unset($t->flags->f->variations[0]->value);
        }];
        yield 'off_variation missing' => ['flags.f.off_variation', static function (stdClass $t): void {
            unset($t->flags->f->off_variation);
        }];
        yield 'off_variation unknown' => ['flags.f.off_variation', static function (stdClass $t): void {
            $t->flags->f->off_variation = 'nope';
        }];
        yield 'prerequisites' => ['flags.f.prerequisites', static function (stdClass $t): void {
            $t->flags->f->prerequisites = new stdClass();
        }];
        yield 'targets' => ['flags.f.targets', static function (stdClass $t): void {
            unset($t->flags->f->targets);
        }];
        yield 'rules' => ['flags.f.rules', static function (stdClass $t): void {
            $t->flags->f->rules = null;
        }];
        yield 'enabled' => ['flags.f.enabled', static function (stdClass $t): void {
            $t->flags->f->enabled = 1;
        }];
        yield 'version bool' => ['flags.f.version', static function (stdClass $t): void {
            $t->flags->f->version = true;
        }];
        yield 'version float' => ['flags.f.version', static function (stdClass $t): void {
            $t->flags->f->version = 1.0;
        }];
        yield 'fallthrough' => ['flags.f.fallthrough', static function (stdClass $t): void {
            unset($t->flags->f->fallthrough);
        }];
        // Prerequisite, target, rule
        yield 'prerequisite flag' => ['flags.f.prerequisites[0].flag', static function (stdClass $t): void {
            $t->flags->f->prerequisites[0]->flag = 1;
        }];
        yield 'prerequisite variation' => ['flags.f.prerequisites[0].variation', static function (stdClass $t): void {
            unset($t->flags->f->prerequisites[0]->variation);
        }];
        yield 'target variation' => ['flags.f.targets[0].variation', static function (stdClass $t): void {
            $t->flags->f->targets[0]->variation = 'nope';
        }];
        yield 'target value' => ['flags.f.targets[0].values[1]', static function (stdClass $t): void {
            $t->flags->f->targets[0]->values[1] = 2;
        }];
        yield 'rule description' => ['flags.f.rules[0].description', static function (stdClass $t): void {
            $t->flags->f->rules[0]->description = 1;
        }];
        yield 'rule clauses' => ['flags.f.rules[0].clauses', static function (stdClass $t): void {
            $t->flags->f->rules[0]->clauses = [];
        }];
        yield 'rule serve' => ['flags.f.rules[0].serve', static function (stdClass $t): void {
            unset($t->flags->f->rules[0]->serve);
        }];
        // Serve and rollout
        yield 'serve empty' => ['flags.f.fallthrough', static function (stdClass $t): void {
            $t->flags->f->fallthrough = new stdClass();
        }];
        yield 'serve both' => ['flags.f.fallthrough', static function (stdClass $t): void {
            $t->flags->f->fallthrough->variation = 'on';
        }];
        yield 'serve variation' => ['flags.f.rules[0].serve.variation', static function (stdClass $t): void {
            $t->flags->f->rules[0]->serve->variation = 'nope';
        }];
        yield 'bucket_by' => ['flags.f.fallthrough.rollout.bucket_by', static function (stdClass $t): void {
            $t->flags->f->fallthrough->rollout->bucket_by = null;
        }];
        yield 'weights empty' => ['flags.f.fallthrough.rollout.weights', static function (stdClass $t): void {
            $t->flags->f->fallthrough->rollout->weights = [];
        }];
        yield 'weight variation' => ['flags.f.fallthrough.rollout.weights[0].variation', static function (stdClass $t): void {
            $t->flags->f->fallthrough->rollout->weights[0]->variation = 'nope';
        }];
        yield 'weight float' => ['flags.f.fallthrough.rollout.weights[0].weight', static function (stdClass $t): void {
            $t->flags->f->fallthrough->rollout->weights[0]->weight = 40000.0;
        }];
        yield 'weight bool' => ['flags.f.fallthrough.rollout.weights[0].weight', static function (stdClass $t): void {
            $t->flags->f->fallthrough->rollout->weights[0]->weight = true;
        }];
        yield 'weight negative' => ['flags.f.fallthrough.rollout.weights[0].weight', static function (stdClass $t): void {
            $t->flags->f->fallthrough->rollout->weights[0]->weight = -1;
        }];
        yield 'weight too big' => ['flags.f.fallthrough.rollout.weights[1].weight', static function (stdClass $t): void {
            $t->flags->f->fallthrough->rollout->weights[1]->weight = 100001;
        }];
        yield 'weights sum' => ['flags.f.fallthrough.rollout.weights', static function (stdClass $t): void {
            $t->flags->f->fallthrough->rollout->weights[1]->weight = 50000;
        }];
        // Clause
        yield 'clause' => ['flags.f.rules[0].clauses[0]', static function (stdClass $t): void {
            $t->flags->f->rules[0]->clauses[0] = 1;
        }];
        yield 'attribute' => ['flags.f.rules[0].clauses[0].attribute', static function (stdClass $t): void {
            self::clauseOf($t)->attribute = 1;
        }];
        yield 'operator' => ['flags.f.rules[0].clauses[0].operator', static function (stdClass $t): void {
            self::clauseOf($t)->operator = 'eq';
        }];
        yield 'values empty' => ['flags.f.rules[0].clauses[0].values', static function (stdClass $t): void {
            self::clauseOf($t)->values = [];
        }];
        yield 'values string' => ['flags.f.rules[0].clauses[0].values', static function (stdClass $t): void {
            self::clauseOf($t)->values = 'pro';
        }];
        yield 'value null' => ['flags.f.rules[0].clauses[0].values[0]', static function (stdClass $t): void {
            self::clauseOf($t)->values = [null];
        }];
        yield 'value list' => ['flags.f.rules[0].clauses[0].values[1]', static function (stdClass $t): void {
            self::clauseOf($t)->values = ['a', ['b']];
        }];
        yield 'value object' => ['flags.f.rules[0].clauses[0].values[0]', static function (stdClass $t): void {
            self::clauseOf($t)->values = [new stdClass()];
        }];
        yield 'negate' => ['flags.f.rules[0].clauses[0].negate', static function (stdClass $t): void {
            self::clauseOf($t)->negate = 'yes';
        }];
        yield 'negate null' => ['flags.f.rules[0].clauses[0].negate', static function (stdClass $t): void {
            self::clauseOf($t)->negate = null;
        }];
        yield 'in_segment in a segment' => ['segments.s.rules[0].clauses[0].operator', static function (stdClass $t): void {
            $t->segments->s->rules[0]->clauses[0] = G::clause('key', 'in_segment', ['s']);
        }];
        // Segment
        yield 'segment' => ['segments.s', static function (stdClass $t): void {
            $t->segments->s = [];
        }];
        yield 'included' => ['segments.s.included', static function (stdClass $t): void {
            unset($t->segments->s->included);
        }];
        yield 'excluded value' => ['segments.s.excluded[0]', static function (stdClass $t): void {
            $t->segments->s->excluded = [1];
        }];
        yield 'segment rules' => ['segments.s.rules', static function (stdClass $t): void {
            $t->segments->s->rules = new stdClass();
        }];
        yield 'segment rule' => ['segments.s.rules[0]', static function (stdClass $t): void {
            $t->segments->s->rules[0] = [];
        }];
        yield 'segment rule clauses' => ['segments.s.rules[0].clauses', static function (stdClass $t): void {
            unset($t->segments->s->rules[0]->clauses);
        }];
    }

    /** @param callable(stdClass): void $change */
    #[DataProvider('errors')]
    public function testErrorNamesThePath(string $path, callable $change): void
    {
        self::assertStringStartsWith("{$path}: ", self::errorFor($change));
    }

    public function testRootMustBeAnObject(): void
    {
        foreach ([[], 'x', null] as $tree) {
            try {
                Compiler::compile($tree);
                self::fail('no ParseException');
            } catch (ParseException $e) {
                self::assertStringStartsWith('ruleset: ', $e->getMessage());
            }
        }
    }

    public function testOperatorErrorSaysUnknownOperator(): void
    {
        self::assertStringStartsWith(
            'flags.f.rules[0].clauses[0].operator: unknown operator',
            self::errorFor(static function (stdClass $t): void {
                self::clauseOf($t)->operator = 'eq';
            }),
        );
    }

    public function testWeightSumErrorNamesTheTotal(): void
    {
        self::assertStringContainsString('100000', self::errorFor(static function (stdClass $t): void {
            $t->flags->f->fallthrough->rollout->weights[1]->weight = 50000;
        }));
    }

    public function testSchemaVersion10InTextIsRefused(): void
    {
        $text = str_replace('"schema_version":1', '"schema_version":1.0', (string) json_encode(self::validTree()));
        $this->expectException(ParseException::class);
        $this->expectExceptionMessageMatches('/\Aschema_version: /');
        Compiler::compileJson($text);
    }

    public function testNullVariationValueIsAllowed(): void
    {
        $tree = self::validTree();
        $tree->flags->f->variations[0]->value = null;
        self::assertSame(['s' => null], Compiler::compile($tree)['flags']['f']['variations']['on']);
    }

    public function testDescriptionIsOptional(): void
    {
        $tree = self::validTree();
        unset($tree->flags->f->rules[0]->description);
        self::assertCount(1, Compiler::compile($tree)['flags']['f']['rules']);
    }

    public function testUnknownFieldsAreIgnored(): void
    {
        $tree = self::validTree();
        $tree->extra = 1;
        $tree->flags->f->extra = (object) ['x' => 1];
        self::clauseOf($tree)->extra = [];
        $tree->segments->s->extra = null;
        self::assertSame(Compiler::compile(self::validTree()), Compiler::compile($tree));
    }

    public function testNegateDefaultsToFalse(): void
    {
        $tree = self::validTree();
        self::clauseOf($tree)->negate = true;
        self::assertTrue(Compiler::compile($tree)['flags']['f']['rules'][0]['clauses'][0]['n']);
        unset(self::clauseOf($tree)->negate);
        self::assertFalse(Compiler::compile($tree)['flags']['f']['rules'][0]['clauses'][0]['n']);
    }

    public function testCrossReferencesAreNotChecked(): void
    {
        $tree = self::validTree();
        $tree->flags->f->prerequisites = [G::prerequisite('missing', 'any')];
        $tree->flags->f->rules[0]->clauses[0] = G::clause('key', 'in_segment', ['missing']);
        self::assertArrayHasKey('f', Compiler::compile($tree)['flags']);
    }

    public function testNumericKeysStayStrings(): void
    {
        $tree = G::ruleset([G::flag('7', ['variations' => [G::variation('1', 'one'), G::variation('0', 'zero')], 'off_variation' => '0', 'fallthrough' => G::serve('1')])]);
        $flag = Compiler::compile($tree)['flags']['7'];
        self::assertSame('0', $flag['off']);
        self::assertSame(['v' => '1'], $flag['fallthrough']);
        self::assertSame(['1', '0'], array_map('strval', array_keys($flag['variations'])));
    }

    /**
     * prepared compiles a ruleset whose clause is ($operator, $values) and
     * returns its prepared values.
     *
     * @param list<mixed> $values
     * @return list<mixed>
     */
    private static function prepared(string $operator, array $values): array
    {
        $tree = self::validTree();
        $tree->flags->f->rules[0]->clauses[0] = G::clause('a', $operator, $values);

        return Compiler::compile($tree)['flags']['f']['rules'][0]['clauses'][0]['p'];
    }

    public function testPreparedValues(): void
    {
        self::assertSame(['a', 1, true], self::prepared('in', ['a', 1, true]));
        self::assertSame(['a', 1, true], self::prepared('not_in', ['a', 1, true]));
        foreach (['contains', 'starts_with', 'ends_with', 'in_segment'] as $op) {
            self::assertSame(['a'], self::prepared($op, ['a', 1, true]), $op);
        }
        self::assertSame([1, 2.5], self::prepared('lt', ['1', 1, 2.5, true]));
        self::assertSame([1], self::prepared('gte', [1]));
        self::assertSame([Semver::parse('1.2.3')], self::prepared('semver_eq', ['1.2.3', 'bad', 1]));
        self::assertSame([0], self::prepared('before', ['1970-01-01T00:00:00Z', 'bad', 5]));
        self::assertSame([1000], self::prepared('after', ['1970-01-01T00:00:01Z']));
        $patterns = self::prepared('matches', ['^a$', 1, '(']);
        self::assertSame([Regex::translate('^a$')], $patterns);
    }

    public function testRegexThatDoesNotCompileIsAWarning(): void
    {
        $tree = self::validTree();
        $tree->flags->f->rules[0]->clauses[0] = G::clause('a', 'matches', ['ok', '(?=x)']);
        $rs = Compiler::compile($tree);
        self::assertCount(1, $rs['warnings']);
        self::assertMatchesRegularExpression(
            '/\Aflags\.f\.rules\[0\]\.clauses\[0\]\.values\[1\]: regex does not compile: /',
            $rs['warnings'][0],
        );
        self::assertSame([Regex::translate('ok')], $rs['flags']['f']['rules'][0]['clauses'][0]['p']);
    }

    public function testCompileDoesNotChangeTheTree(): void
    {
        $tree = self::validTree();
        $before = json_encode($tree);
        Compiler::compile($tree);
        self::assertSame($before, json_encode($tree));
    }
}
