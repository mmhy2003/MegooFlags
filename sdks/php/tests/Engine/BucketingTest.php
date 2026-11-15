<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Engine;

use MegooFlags\Internal\Engine\Bucketing;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class BucketingTest extends TestCase
{
    /** @return iterable<string, array{string, string}> */
    public static function texts(): iterable
    {
        yield 'string' => ['"u"', 'u'];
        yield 'int' => ['42', '42'];
        yield 'integral float' => ['42.0', '42'];
        yield 'negative zero' => ['-0.0', '0'];
        yield 'negative int' => ['-7', '-7'];
        yield 'max safe' => ['9007199254740991', '9007199254740991'];
        yield 'min safe float' => ['-9007199254740991.0', '-9007199254740991'];
        yield 'exponent' => ['1e3', '1000'];
    }

    #[DataProvider('texts')]
    public function testBucketText(string $json, string $expected): void
    {
        self::assertSame($expected, Bucketing::bucketText(json_decode($json, false)));
    }

    /** @return iterable<string, array{string}> */
    public static function refused(): iterable
    {
        yield 'fraction' => ['42.5'];
        yield 'true' => ['true'];
        yield 'false' => ['false'];
        yield 'null' => ['null'];
        yield 'array' => ['[1]'];
        yield 'object' => ['{"a":1}'];
        yield 'above max safe' => ['9007199254740992'];
        yield 'above max safe float' => ['9007199254740992.0'];
        yield 'below min safe' => ['-9007199254740992'];
        yield 'overflowing float' => ['1e400'];
    }

    #[DataProvider('refused')]
    public function testBucketTextRefused(string $json): void
    {
        self::assertNull(Bucketing::bucketText(json_decode($json, false)));
    }

    public function testBucketTextNonFinite(): void
    {
        self::assertNull(Bucketing::bucketText(NAN));
        self::assertNull(Bucketing::bucketText(INF));
    }

    public function testBucket(): void
    {
        self::assertSame(68258, Bucketing::bucket('f', 'u'));
    }

    public function testPickVariation(): void
    {
        $weights = [['a', 30000], ['b', 40000], ['c', 30000]];

        self::assertSame('a', Bucketing::pickVariation($weights, 29999));
        self::assertSame('b', Bucketing::pickVariation($weights, 30000));
        self::assertSame('b', Bucketing::pickVariation($weights, 68258));
        self::assertSame('c', Bucketing::pickVariation($weights, 99999));
        self::assertSame('b', Bucketing::pickVariation([['a', 0], ['b', 100000]], 0));
    }
}
