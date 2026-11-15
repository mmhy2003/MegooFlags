<?php

declare(strict_types=1);

namespace MegooFlags\Tests;

use InvalidArgumentException;
use MegooFlags\Internal\Engine\Bucketing;
use MegooFlags\Internal\Engine\Compiler;
use MegooFlags\Internal\Engine\Evaluator;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use stdClass;

/**
 * Runs the language-neutral suite in spec/conformance against the engine:
 * one test per <file stem>::<name> and per bucketing::<input>. Files are
 * decoded with objects, so {} (stdClass) differs from [] (a list).
 */
final class ConformanceTest extends TestCase
{
    private const CONFORMANCE = __DIR__ . '/../../../spec/conformance';
    private const SCHEMA_VERSION = 1;

    /** @var array<string, array<string, mixed>> compiled rulesets by case file */
    private static array $compiled = [];

    /** loadSuite reads a suite file, refusing any schema_version but the integer 1. */
    public static function loadSuite(string $path): stdClass
    {
        $data = json_decode((string) file_get_contents($path), false, 512, JSON_THROW_ON_ERROR);
        if (!$data instanceof stdClass) {
            throw new InvalidArgumentException(basename($path) . ': not an object');
        }
        $version = $data->schema_version ?? null;
        if ($version !== self::SCHEMA_VERSION) {
            throw new InvalidArgumentException(basename($path) . ': unknown schema_version ' . json_encode($version));
        }

        return $data;
    }

    /** sameJson is JSON equality: 1 == 1.0, but true != 1, "1" != 1 and {} != []. */
    public static function sameJson(mixed $left, mixed $right): bool
    {
        if (is_bool($left) || is_bool($right) || $left === null || $right === null) {
            return $left === $right;
        }
        if (is_int($left) || is_float($left)) {
            return (is_int($right) || is_float($right)) && $left == $right;
        }
        if (is_string($left)) {
            return is_string($right) && $left === $right;
        }
        if (is_array($left)) {
            if (!is_array($right) || count($left) !== count($right)) {
                return false;
            }
            foreach ($left as $i => $item) {
                if (!self::sameJson($item, $right[$i])) {
                    return false;
                }
            }

            return true;
        }
        if ($left instanceof stdClass && $right instanceof stdClass) {
            $l = get_object_vars($left);
            $r = get_object_vars($right);
            if (count($l) !== count($r)) {
                return false;
            }
            foreach ($l as $key => $value) {
                if (!array_key_exists($key, $r) || !self::sameJson($value, $r[$key])) {
                    return false;
                }
            }

            return true;
        }

        return false;
    }

    /** @return list<string> */
    private static function caseFiles(): array
    {
        $files = glob(self::CONFORMANCE . '/cases/*.json') ?: [];
        sort($files);

        return $files;
    }

    /** @return list<stdClass> */
    private static function vectors(): array
    {
        $path = self::CONFORMANCE . '/bucketing.json';

        return is_file($path) ? self::loadSuite($path)->vectors : [];
    }

    /** @return iterable<string, array{string, stdClass}> */
    public static function cases(): iterable
    {
        foreach (self::caseFiles() as $path) {
            $stem = basename($path, '.json');
            foreach (self::loadSuite($path)->tests as $case) {
                yield "{$stem}::{$case->name}" => [$path, $case];
            }
        }
    }

    /** @return iterable<string, array{stdClass}> */
    public static function bucketingVectors(): iterable
    {
        foreach (self::vectors() as $vector) {
            yield "bucketing::{$vector->input}" => [$vector];
        }
    }

    /** @return array<string, mixed> the file's ruleset, compiled once; a parse error fails each test of the file */
    private static function rulesetFor(string $path): array
    {
        return self::$compiled[$path] ??= Compiler::compile(self::loadSuite($path)->ruleset);
    }

    #[DataProvider('cases')]
    public function testCase(string $path, stdClass $case): void
    {
        $result = (new Evaluator(false))->evaluate(
            self::rulesetFor($path),
            $case->flag,
            $case->context,
            $case->default,
            $case->expected_type ?? null,
        );
        $expected = $case->expected;
        self::assertTrue(
            self::sameJson($result->value, $expected->value),
            'value ' . json_encode($result->value) . ' != ' . json_encode($expected->value),
        );
        self::assertSame($expected->variant, $result->variant, 'variant');
        self::assertSame($expected->reason, $result->reason, 'reason');
        self::assertSame($expected->error_code, $result->errorCode, 'error_code');
        $metadata = $result->metadata();
        foreach (get_object_vars($expected->metadata) as $key => $value) {
            self::assertArrayHasKey($key, $metadata);
            self::assertTrue(self::sameJson($metadata[$key], $value), "metadata {$key}");
        }
    }

    #[DataProvider('bucketingVectors')]
    public function testBucketing(stdClass $vector): void
    {
        [$flag, $text] = explode('.', $vector->input, 2);
        self::assertSame($vector->bucket, Bucketing::bucket($flag, $text));
    }

    public function testHasCaseFilesAndVectors(): void
    {
        self::assertNotEmpty(self::caseFiles());
        self::assertGreaterThanOrEqual(20, count(self::vectors()));
    }

    public function testOtherSchemaVersionsAreRefused(): void
    {
        $path = (string) tempnam(sys_get_temp_dir(), 'megooflags-cases-');
        try {
            foreach (['2', '1.0', '"1"', 'true', 'null'] as $version) {
                file_put_contents($path, "{\"schema_version\": {$version}, \"tests\": []}");
                try {
                    self::loadSuite($path);
                    self::fail("schema_version {$version} was accepted");
                } catch (InvalidArgumentException $e) {
                    self::assertStringContainsString('unknown schema_version', $e->getMessage());
                }
            }
        } finally {
            unlink($path);
        }
    }

    /** @return iterable<string, array{string, string, bool}> */
    public static function sameJsonPairs(): iterable
    {
        yield '1 and 1.0' => ['1', '1.0', true];
        yield 'true and 1' => ['true', '1', false];
        yield '"1" and 1' => ['"1"', '1', false];
        yield 'null and null' => ['null', 'null', true];
        yield 'null and false' => ['null', 'false', false];
        yield 'nested' => ['[1, {"a": true}]', '[1.0, {"a": true}]', true];
        yield 'lengths' => ['[1]', '[1, 2]', false];
        yield 'keys' => ['{"a": 1}', '{"a": 1, "b": 2}', false];
        yield '0 and false' => ['{"a": 0}', '{"a": false}', false];
        yield 'object and array' => ['{}', '[]', false];
    }

    #[DataProvider('sameJsonPairs')]
    public function testSameJson(string $left, string $right, bool $equal): void
    {
        self::assertSame($equal, self::sameJson(json_decode($left, false), json_decode($right, false)));
        self::assertSame($equal, self::sameJson(json_decode($right, false), json_decode($left, false)));
    }
}
