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
 * 60 s was left by a shipper that died and is claimed again.
 *
 * @internal
 */
final class Spool
{
    public const MAX_BYTES = 8 * 1024 * 1024;

    public const STALE_AFTER = 60;

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
        $created = $size === false;
        if (@file_put_contents($this->path, $line . "\n", FILE_APPEND | LOCK_EX) === false) {
            return false;
        }
        if ($created) {
            @chmod($this->path, 0o600);
        }

        return true;
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
        $target = $this->claimName();
        if (@rename($this->path, $target)) {
            $claimed[] = $target;
        }
        $limit = time() - self::STALE_AFTER;
        foreach (glob($this->prefix . '*.sending') ?: [] as $file) {
            $mtime = @filemtime($file);
            if ($mtime === false || $mtime > $limit) {
                continue;
            }
            $target = $this->claimName();
            if (@rename($file, $target)) {
                @touch($target);
                $claimed[] = $target;
            }
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

    private function claimName(): string
    {
        return $this->prefix . getmypid() . '.' . bin2hex(random_bytes(6)) . '.sending';
    }
}
