<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Daemon;

use MegooFlags\Internal\Daemon\Lru;
use PHPUnit\Framework\TestCase;

final class LruTest extends TestCase
{
    public function testSeenIsFalseTheFirstTimeAndTrueAfter(): void
    {
        $lru = new Lru(3);

        self::assertFalse($lru->seen('a'));
        self::assertTrue($lru->seen('a'));
        self::assertFalse($lru->seen('b'));
        self::assertSame(2, $lru->count());
    }

    public function testTheOldestKeyIsEvictedAtCapacity(): void
    {
        $lru = new Lru(2);
        $lru->seen('a');
        $lru->seen('b');

        self::assertFalse($lru->seen('c'));
        self::assertSame(2, $lru->count());
        self::assertTrue($lru->seen('b'));
        self::assertTrue($lru->seen('c'));
        self::assertFalse($lru->seen('a'), 'a was evicted');
    }

    public function testASeenKeyIsRefreshed(): void
    {
        $lru = new Lru(2);
        $lru->seen('a');
        $lru->seen('b');
        $lru->seen('a');

        $lru->seen('c');

        self::assertTrue($lru->seen('a'), 'a was refreshed, b was the oldest');
        self::assertFalse($lru->seen('b'));
    }

    public function testNumericKeysStayStrings(): void
    {
        $lru = new Lru(2);

        self::assertFalse($lru->seen('1'));
        self::assertTrue($lru->seen('1'));
        self::assertFalse($lru->seen('01'));
    }
}
