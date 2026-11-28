<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Engine;

use MegooFlags\Internal\Engine\Dates;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class DatesTest extends TestCase
{
    /** @return iterable<string, array{string, int}> */
    public static function valid(): iterable
    {
        yield 'epoch' => ['"1970-01-01T00:00:00Z"', 0];
        yield 'nanoseconds truncated' => ['"1970-01-01T00:00:00.123456789Z"', 123];
        yield 'one fraction digit' => ['"1970-01-01T00:00:00.5Z"', 500];
        yield 'offset' => ['"1970-01-01T01:00:00+01:00"', 0];
        yield 'negative offset' => ['"1969-12-31T23:00:00-01:00"', 0];
        yield 'before epoch' => ['"1969-12-31T23:59:59Z"', -1000];
        yield 'lower case' => ['"2026-01-01t00:00:00z"', 1767225600000];
        yield 'space' => ['"2026-01-01 00:00:00Z"', 1767225600000];
        yield 'leap day' => ['"2024-02-29T00:00:00Z"', 1709164800000];
        yield 'largest offset' => ['"1970-01-01T23:59:00+23:59"', 0];
        yield 'year one' => ['"0001-01-01T00:00:00Z"', -62135596800000];
    }

    #[DataProvider('valid')]
    public function testValid(string $json, int $expected): void
    {
        $text = json_decode($json, false);
        self::assertIsString($text);
        self::assertSame($expected, Dates::parseRfc3339Millis($text));
    }

    /** @return iterable<string, array{string}> */
    public static function invalid(): iterable
    {
        yield 'date only' => ['"2026-01-01"'];
        yield 'day out of month' => ['"2026-02-30T00:00:00Z"'];
        yield 'not a leap year' => ['"2025-02-29T00:00:00Z"'];
        yield 'century not a leap year' => ['"1900-02-29T00:00:00Z"'];
        yield 'month 13' => ['"2026-13-01T00:00:00Z"'];
        yield 'hour 24' => ['"2026-01-01T24:00:00Z"'];
        yield 'second 60' => ['"2026-01-01T00:00:60Z"'];
        yield 'minute 60' => ['"2026-01-01T00:60:00Z"'];
        yield 'no zone' => ['"2026-01-01T00:00:00"'];
        yield 'offset 24h' => ['"2026-01-01T00:00:00+24:00"'];
        yield 'year zero' => ['"0000-01-01T00:00:00Z"'];
        yield 'empty fraction' => ['"2026-01-01T00:00:00.Z"'];
        yield 'trailing newline' => ['"2026-01-01T00:00:00Z\n"'];
        yield 'non-ascii digits' => ['"2026-01-01T00:00:0٣Z"'];
        yield 'empty' => ['""'];
    }

    #[DataProvider('invalid')]
    public function testInvalid(string $json): void
    {
        $text = json_decode($json, false);
        self::assertIsString($text);
        self::assertNull(Dates::parseRfc3339Millis($text));
    }

    /** @return iterable<string, array{string, int}> */
    public static function millis(): iterable
    {
        yield 'float floored' => ['1500.9', 1500];
        yield 'negative floored' => ['-0.5', -1];
        yield 'int' => ['1500', 1500];
        yield 'string' => ['"1970-01-01T00:00:01Z"', 1000];
        yield 'huge float clamped' => ['1e300', PHP_INT_MAX];
        yield 'huge negative float clamped' => ['-1e300', PHP_INT_MIN];
        yield 'two to the 63 clamped' => ['9223372036854775808.0', PHP_INT_MAX];
    }

    #[DataProvider('millis')]
    public function testToMillis(string $json, int $expected): void
    {
        self::assertSame($expected, Dates::toMillis(json_decode($json, false)));
    }

    /** @return iterable<string, array{string}> */
    public static function notMillis(): iterable
    {
        yield 'true' => ['true'];
        yield 'null' => ['null'];
        yield 'array' => ['[1]'];
        yield 'object' => ['{}'];
        yield 'bad string' => ['"yesterday"'];
        yield 'overflowing float' => ['1e400'];
    }

    #[DataProvider('notMillis')]
    public function testToMillisInvalid(string $json): void
    {
        self::assertNull(Dates::toMillis(json_decode($json, false)));
    }

    public function testToMillisNonFinite(): void
    {
        self::assertNull(Dates::toMillis(NAN));
        self::assertNull(Dates::toMillis(INF));
        self::assertNull(Dates::toMillis(-INF));
    }
}
