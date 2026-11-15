<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Cache;

/**
 * Lock is an exclusive, non-blocking flock on the lock file of one SDK
 * key, so that only one request (or the daemon) does upkeep at a time. The
 * lock is released by release(), by the destructor, or when the process
 * ends.
 *
 * @internal
 */
final class Lock
{
    /** @var resource|null */
    private $handle = null;

    public function __construct(private readonly string $path) {}

    /** tryAcquire takes the lock without waiting; true when this object holds it. */
    public function tryAcquire(): bool
    {
        if ($this->handle !== null) {
            return true;
        }
        try {
            CacheFiles::ensureDir(dirname($this->path));
        } catch (\RuntimeException) {
            return false;
        }
        $created = !is_file($this->path);
        $handle = @fopen($this->path, 'c');
        if ($handle === false) {
            return false;
        }
        if ($created) {
            @chmod($this->path, 0o600);
        }
        if (!flock($handle, LOCK_EX | LOCK_NB)) {
            fclose($handle);

            return false;
        }
        $this->handle = $handle;

        return true;
    }

    public function held(): bool
    {
        return $this->handle !== null;
    }

    public function release(): void
    {
        if ($this->handle === null) {
            return;
        }
        flock($this->handle, LOCK_UN);
        fclose($this->handle);
        $this->handle = null;
    }

    public function __destruct()
    {
        $this->release();
    }
}
