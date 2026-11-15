<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Cache;

use MegooFlags\Internal\Cache\Lock;
use PHPUnit\Framework\TestCase;

final class LockTest extends TestCase
{
    private string $dir;

    private string $path;

    protected function setUp(): void
    {
        $this->dir = sys_get_temp_dir() . '/megooflags-lock-' . bin2hex(random_bytes(6));
        $this->path = $this->dir . '/megooflags-0123456789abcdef.lock';
    }

    protected function tearDown(): void
    {
        if (is_file($this->path)) {
            unlink($this->path);
        }
        if (is_dir($this->dir)) {
            rmdir($this->dir);
        }
    }

    public function testSecondNonBlockingAcquireFailsUntilRelease(): void
    {
        $first = new Lock($this->path);
        $second = new Lock($this->path);

        self::assertTrue($first->tryAcquire());
        self::assertTrue($first->held());
        self::assertFalse($second->tryAcquire());
        self::assertFalse($second->held());

        $first->release();

        self::assertFalse($first->held());
        self::assertTrue($second->tryAcquire());
        self::assertFalse($first->tryAcquire());
        $second->release();
    }

    public function testCreatesThePrivateLockFile(): void
    {
        $lock = new Lock($this->path);

        self::assertTrue($lock->tryAcquire());

        self::assertSame(0o600, fileperms($this->path) & 0o777);
        self::assertSame(0o700, fileperms($this->dir) & 0o777);
        $lock->release();
    }

    public function testAcquireTwiceAndReleaseTwiceAreSafe(): void
    {
        $lock = new Lock($this->path);

        self::assertTrue($lock->tryAcquire());
        self::assertTrue($lock->tryAcquire());
        $lock->release();
        $lock->release();

        self::assertTrue((new Lock($this->path))->tryAcquire());
    }

    public function testDestructorReleases(): void
    {
        $lock = new Lock($this->path);
        self::assertTrue($lock->tryAcquire());
        unset($lock);

        self::assertTrue((new Lock($this->path))->tryAcquire());
    }

    public function testLockIsHeldAgainstAnotherProcess(): void
    {
        $lock = new Lock($this->path);
        self::assertTrue($lock->tryAcquire());

        $code = sprintf(
            '$f = fopen(%s, "c"); exit(flock($f, LOCK_EX | LOCK_NB) ? 0 : 3);',
            var_export($this->path, true),
        );
        exec(escapeshellarg(PHP_BINARY) . ' -r ' . escapeshellarg($code), $output, $status);

        self::assertSame(3, $status);
        $lock->release();
    }
}
