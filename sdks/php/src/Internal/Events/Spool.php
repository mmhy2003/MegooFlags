<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Events;

use MegooFlags\Internal\Cache\CacheFiles;
use RuntimeException;

/**
 * Spool is the events file of one SDK key, megooflags-H.events.jsonl: one
 * JSON line per request, appended under LOCK_EX. A line is dropped when the
 * file is already 8 MiB or larger.
 *
 * A shipper claims the spool by renaming it to
 * megooflags-H.events.<pid>.<random>.sending, so appends go to a new file
 * and two shippers never send the same lines. A claimed file older than
 * 60 s was left by a shipper that died and is claimed again; a claim
 * touches the file, so a fresh claim is never taken for an abandoned one.
 * Appends and claims share the file's LOCK_EX: a claim waits for a writer
 * that already holds it, and a writer that gets it after the rename
 * reopens the path, so no line lands in a file that was already read.
 *
 * @internal
 */
final class Spool
{
    public const MAX_BYTES = 8 * 1024 * 1024;

    public const STALE_AFTER = 60;

    private const OPEN_ATTEMPTS = 5;

    private const SUFFIX = '.events.jsonl';

    private readonly string $prefix;

    public function __construct(private readonly string $path)
    {
        $this->prefix = str_ends_with($path, self::SUFFIX)
            ? substr($path, 0, -strlen('jsonl'))
            : $path . '.';
    }

    /** append adds $line and a newline; false when the line was dropped. */
    public function append(string $line): bool
    {
        clearstatcache(true, $this->path);
        $size = @filesize($this->path);
        if ($size !== false && $size >= self::MAX_BYTES) {
            return false;
        }
        try {
            CacheFiles::ensureDir(dirname($this->path));
        } catch (RuntimeException) {
            return false;
        }
        $handle = $this->openLocked();
        if ($handle === null) {
            return false;
        }
        $data = $line . "\n";
        $ok = fwrite($handle, $data) === strlen($data) && fflush($handle);
        flock($handle, LOCK_UN);
        fclose($handle);

        return $ok;
    }

    /**
     * openLocked opens the spool for appending under LOCK_EX. When a
     * shipper renamed the file between the open and the lock, the handle
     * points at the claimed file: it is closed and the new spool opened, so
     * no line is appended to a file that was already read.
     *
     * @return resource|null
     */
    private function openLocked()
    {
        for ($attempt = 0; $attempt < self::OPEN_ATTEMPTS; $attempt++) {
            $created = !is_file($this->path);
            $handle = @fopen($this->path, 'a');
            if ($handle === false) {
                return null;
            }
            if ($created) {
                @chmod($this->path, 0o600);
            }
            if (!flock($handle, LOCK_EX)) {
                fclose($handle);

                return null;
            }
            clearstatcache(true, $this->path);
            $opened = fstat($handle);
            $current = @stat($this->path);
            if ($opened !== false && $current !== false && $opened['ino'] === $current['ino'] && $opened['dev'] === $current['dev']) {
                return $handle;
            }
            flock($handle, LOCK_UN);
            fclose($handle);
        }

        return null;
    }

    /**
     * claim renames the spool and every stale claimed file to new claimed
     * names and returns them; nothing to claim gives [].
     *
     * @return list<string>
     */
    public function claim(): array
    {
        $claimed = [];
        $limit = time() - self::STALE_AFTER;
        // Stale files first, so the spool claimed below is never among them.
        foreach (glob($this->prefix . '*.sending') ?: [] as $file) {
            clearstatcache(true, $file);
            $mtime = @filemtime($file);
            if ($mtime === false || $mtime > $limit) {
                continue;
            }
            $target = $this->claimName();
            if (@rename($file, $target)) {
                self::settle($target);
                $claimed[] = $target;
            }
        }
        $target = $this->claimName();
        if (@rename($this->path, $target)) {
            self::settle($target);
            $claimed[] = $target;
        }

        return $claimed;
    }

    /**
     * lines reads the non-empty lines of claimed files.
     *
     * @param list<string> $claimed
     * @return list<string>
     */
    public function lines(array $claimed): array
    {
        $lines = [];
        foreach ($claimed as $file) {
            foreach (@file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [] as $line) {
                $lines[] = $line;
            }
        }

        return $lines;
    }

    /** @param list<string> $claimed */
    public function delete(array $claimed): void
    {
        foreach ($claimed as $file) {
            @unlink($file);
        }
    }

    /**
     * settle prepares a file just claimed: a fresh mtime, so that it does
     * not look abandoned while it is being sent, then LOCK_EX taken and
     * released, so that a writer which locked the file before the rename
     * has finished its line before the file is read. Writers that lock it
     * later see that it was renamed and append to the new spool instead.
     */
    private static function settle(string $file): void
    {
        @touch($file);
        $handle = @fopen($file, 'r');
        if ($handle === false) {
            return;
        }
        flock($handle, LOCK_EX);
        flock($handle, LOCK_UN);
        fclose($handle);
    }

    private function claimName(): string
    {
        return $this->prefix . getmypid() . '.' . bin2hex(random_bytes(6)) . '.sending';
    }
}
