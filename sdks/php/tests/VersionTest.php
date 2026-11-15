<?php

declare(strict_types=1);

namespace MegooFlags\Tests;

use MegooFlags\Version;
use PHPUnit\Framework\TestCase;

final class VersionTest extends TestCase
{
    public function testVersion(): void
    {
        self::assertSame('0.1.0', Version::VERSION);
    }
}
