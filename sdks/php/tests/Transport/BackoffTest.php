<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Transport;

use MegooFlags\Internal\Transport\Backoff;
use PHPUnit\Framework\TestCase;

final class BackoffTest extends TestCase
{
    public function testDoublesUpToMaxWithFullJitterFactor(): void
    {
        $waits = [];
        for ($failures = 1; $failures <= 7; $failures++) {
            $waits[] = Backoff::delay($failures, 3.0, 60.0, 1.0);
        }

        self::assertSame([3.0, 6.0, 12.0, 24.0, 48.0, 60.0, 60.0], $waits);
    }

    public function testZeroRandScalesByPointEightFromTheSecondWait(): void
    {
        self::assertSame(3.0, Backoff::delay(1, 3.0, 60.0, 0.0));
        self::assertEqualsWithDelta(4.8, Backoff::delay(2, 3.0, 60.0, 0.0), 1e-9);
        self::assertEqualsWithDelta(48.0, Backoff::delay(9, 3.0, 60.0, 0.0), 1e-9);
    }

    public function testNeverAboveMax(): void
    {
        self::assertSame(2.0, Backoff::delay(1, 5.0, 2.0, 1.0));
        foreach ([1, 2, 10, 64, 1000, PHP_INT_MAX] as $failures) {
            self::assertLessThanOrEqual(60.0, Backoff::delay($failures, 2.0, 60.0, 1.0));
        }
    }

    public function testNoFailuresIsNoWait(): void
    {
        self::assertSame(0.0, Backoff::delay(0, 3.0, 60.0, 1.0));
        self::assertSame(0.0, Backoff::delay(-2, 3.0, 60.0, 1.0));
    }
}
