<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Cache;

use MegooFlags\Internal\Engine\Compiler;
use RuntimeException;
use stdClass;
use Throwable;

/**
 * CacheFiles names and reads and writes the files of one SDK key in the
 * cache directory. Every name is megooflags-<H>.<suffix>, where H is the
 * first 16 hex characters of sha256 of the key, so the key itself is never
 * written to disk.
 *
 * - megooflags-H.json: {"etag", "ruleset"}, the Go SDK's cache format.
 * - megooflags-H.php: the compiled ruleset, written with var_export so
 *   requests load it with require and OPcache keeps it in shared memory.
 *
 * Writes go to a temp file in the same directory (mode 0600) renamed over
 * the target, so a reader never sees a partial file. A missing, corrupt or
 * half-written file loads as null.
 *
 * @internal
 *
 * @phpstan-import-type Ruleset from Compiler
 * @phpstan-type Compiled array{etag: string, source: 'network'|'bootstrap', ruleset: Ruleset}
 */
final class CacheFiles
{
    public const SOURCE_NETWORK = 'network';
    public const SOURCE_BOOTSTRAP = 'bootstrap';

    private readonly string $prefix;

    public function __construct(private readonly string $dir, string $sdkKey)
    {
        $this->prefix = rtrim($dir, '/') . '/megooflags-' . self::hash($sdkKey);
    }

    /** hash is the first 16 hex characters of sha256 of the key. */
    public static function hash(string $sdkKey): string
    {
        return substr(hash('sha256', $sdkKey), 0, 16);
    }

    public function dir(): string
    {
        return $this->dir;
    }

    public function jsonPath(): string
    {
        return $this->prefix . '.json';
    }

    public function compiledPath(): string
    {
        return $this->prefix . '.php';
    }

    public function statePath(): string
    {
        return $this->prefix . '.state.json';
    }

    public function lockPath(): string
    {
        return $this->prefix . '.lock';
    }

    public function spoolPath(): string
    {
        return $this->prefix . '.events.jsonl';
    }

    /**
     * loadJson returns the JSON cache, with the ruleset decoded as
     * json_decode($text, false) does, or null when the file is missing or
     * corrupt.
     *
     * @return array{etag: string, ruleset: mixed}|null
     */
    public function loadJson(): ?array
    {
        $text = @file_get_contents($this->jsonPath());
        if ($text === false) {
            return null;
        }
        try {
            $file = json_decode($text, false, 512, JSON_THROW_ON_ERROR);
        } catch (Throwable) {
            return null;
        }
        if (!$file instanceof stdClass || !property_exists($file, 'ruleset')) {
            return null;
        }
        $etag = $file->etag ?? '';
        if (!is_string($etag)) {
            return null;
        }

        return ['etag' => $etag, 'ruleset' => $file->ruleset];
    }

    /**
     * loadCompiled requires the compiled file, or returns null when it is
     * missing, half-written or not in the expected shape. With $invalidate
     * the OPcache entry is dropped first, so a newer file is read.
     *
     * @return Compiled|null
     */
    public function loadCompiled(bool $invalidate = false): ?array
    {
        $path = $this->compiledPath();
        if (!is_file($path)) {
            return null;
        }
        if ($invalidate) {
            self::invalidate($path);
        }
        // A damaged file must neither break the request nor print anything.
        ob_start();
        try {
            $data = (static fn(string $file): mixed => require $file)($path);
        } catch (Throwable) {
            return null;
        } finally {
            ob_end_clean();
        }
        if (
            !is_array($data)
            || !is_string($data['etag'] ?? null)
            || !in_array($data['source'] ?? null, [self::SOURCE_NETWORK, self::SOURCE_BOOTSTRAP], true)
            || !is_array($data['ruleset'] ?? null)
        ) {
            return null;
        }

        /** @var Compiled $data */
        return $data;
    }

    /**
     * saveNetwork writes a ruleset received from the server: the JSON cache
     * with $raw embedded as received, then the compiled file.
     *
     * @param Ruleset $compiled
     */
    public function saveNetwork(string $raw, string $etag, array $compiled): void
    {
        if (!json_validate($raw)) {
            throw new RuntimeException('megooflags: cannot cache an invalid ruleset');
        }
        $etagJson = json_encode($etag, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE);
        self::writeAtomic($this->jsonPath(), '{"etag":' . $etagJson . ',"ruleset":' . $raw . "}\n");
        $this->writeCompiled($etag, self::SOURCE_NETWORK, $compiled);
    }

    /**
     * saveBootstrap writes the compiled file only, with an empty etag, so
     * the first network check downloads the ruleset.
     *
     * @param Ruleset $compiled
     */
    public function saveBootstrap(array $compiled): void
    {
        $this->writeCompiled('', self::SOURCE_BOOTSTRAP, $compiled);
    }

    /**
     * writeAtomic writes $content to a temp file next to $path (mode 0600)
     * and renames it over $path, creating the directory (mode 0700). It
     * leaves no temp file behind and throws RuntimeException on failure.
     */
    public static function writeAtomic(string $path, string $content): void
    {
        $dir = dirname($path);
        self::ensureDir($dir);
        $tmp = $dir . '/.megooflags-' . bin2hex(random_bytes(8)) . '.tmp';
        $handle = @fopen($tmp, 'x');
        if ($handle === false) {
            throw new RuntimeException('megooflags: cannot create a temp file in ' . $dir);
        }
        $ok = chmod($tmp, 0o600)
            && fwrite($handle, $content) === strlen($content)
            && fflush($handle)
            && fsync($handle);
        $ok = fclose($handle) && $ok;
        if (!$ok || !@rename($tmp, $path)) {
            @unlink($tmp);

            throw new RuntimeException('megooflags: cannot write ' . $path);
        }
    }

    /** ensureDir creates $dir with mode 0700 when it does not exist. */
    public static function ensureDir(string $dir): void
    {
        if (is_dir($dir)) {
            return;
        }
        if (!@mkdir($dir, 0o700, true) && !is_dir($dir)) {
            throw new RuntimeException('megooflags: cannot create the cache directory ' . $dir);
        }
        @chmod($dir, 0o700);
    }

    /**
     * @param Ruleset $compiled
     */
    private function writeCompiled(string $etag, string $source, array $compiled): void
    {
        $data = ['etag' => $etag, 'source' => $source, 'ruleset' => $compiled];
        $path = $this->compiledPath();
        self::writeAtomic($path, "<?php\n\nreturn " . var_export($data, true) . ";\n");
        self::invalidate($path);
    }

    private static function invalidate(string $path): void
    {
        if (function_exists('opcache_invalidate')) {
            @opcache_invalidate($path, true);
        }
    }
}
