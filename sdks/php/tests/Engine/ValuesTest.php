<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Engine;

use MegooFlags\Internal\Engine\Values;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class ValuesTest extends TestCase
{
    /** @return iterable<string, array{string, bool}> */
    public static function numbers(): iterable
    {
        yield 'int' => ['1', true];
        yield 'float' => ['1.5', true];
        yield 'exponent' => ['1e3', true];
        yield 'true' => ['true', false];
        yield 'false' => ['false', false];
        yield 'null' => ['null', false];
        yield 'string' => ['"1"', false];
        yield 'array' => ['[1]', false];
        yield 'object' => ['{}', false];
    }

    #[DataProvider('numbers')]
    public function testIsNumber(string $json, bool $expected): void
    {
        self::assertSame($expected, Values::isNumber(json_decode($json, false)));
    }

    /** @return iterable<string, array{string, bool}> */
    public static function scalars(): iterable
    {
        yield 'string' => ['"a"', true];
        yield 'true' => ['true', true];
        yield 'false' => ['false', true];
        yield 'int' => ['3', true];
        yield 'float' => ['3.5', true];
        yield 'null' => ['null', false];
        yield 'array' => ['["a"]', false];
        yield 'object' => ['{"a":1}', false];
        yield 'empty object' => ['{}', false];
    }

    #[DataProvider('scalars')]
    public function testIsScalar(string $json, bool $expected): void
    {
        self::assertSame($expected, Values::isScalar(json_decode($json, false)));
    }

    /** @return iterable<string, array{string, string, bool}> */
    public static function equalities(): iterable
    {
        yield 'int and float' => ['1', '1.0', true];
        yield 'float and int' => ['1.0', '1', true];
        yield 'true and 1' => ['true', '1', false];
        yield '1 and true' => ['1', 'true', false];
        yield 'string and number' => ['"1"', '1', false];
        yield 'null and null' => ['null', 'null', false];
        yield 'false and false' => ['false', 'false', true];
        yield 'true and false' => ['true', 'false', false];
        yield 'false and 0' => ['false', '0', false];
        yield 'strings' => ['"a"', '"a"', true];
        yield 'different strings' => ['"a"', '"b"', false];
        yield 'different ints' => ['1', '2', false];
        yield 'large ints compare exactly' => ['9007199254740993', '9007199254740992', false];
        yield 'equal large ints' => ['9007199254740993', '9007199254740993', true];
        yield 'arrays' => ['[1]', '[1]', false];
        yield 'objects' => ['{}', '{}', false];
    }

    #[DataProvider('equalities')]
    public function testJsonEqual(string $a, string $b, bool $expected): void
    {
        self::assertSame($expected, Values::jsonEqual(json_decode($a, false), json_decode($b, false)));
    }
}
