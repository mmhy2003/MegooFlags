<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Cache;

use Throwable;

/**
 * State is the shared state file of one SDK key: when the ruleset was last
 * checked, the ETag of the cached ruleset, the failure count and the time
 * of the next allowed attempt (Unix seconds), and the fatal marker ('401',
 * '403' or 'revoked') once the server refused the key.
 *
 * @internal
 */
final class State
{
    public const FATAL_MARKERS = ['401', '403', 'revoked'];

    public function __construct(
        public float $checkedAt = 0.0,
        public string $etag = '',
        public int $failures = 0,
        public float $nextAttempt = 0.0,
        public ?string $fatal = null,
    ) {}

    /** load reads the state file; a missing or corrupt file gives the defaults. */
    public static function load(string $path): self
    {
        $text = @file_get_contents($path);
        if ($text === false) {
            return new self();
        }
        try {
            $data = json_decode($text, true, 512, JSON_THROW_ON_ERROR);
        } catch (Throwable) {
            return new self();
        }
        if (!is_array($data) || array_is_list($data)) {
            return new self();
        }
        $checkedAt = $data['checked_at'] ?? 0.0;
        $etag = $data['etag'] ?? '';
        $failures = $data['failures'] ?? 0;
        $nextAttempt = $data['next_attempt'] ?? 0.0;
        $fatal = $data['fatal'] ?? null;
        if (
            !(is_int($checkedAt) || is_float($checkedAt))
            || !is_string($etag)
            || !is_int($failures)
            || !(is_int($nextAttempt) || is_float($nextAttempt))
            || !($fatal === null || in_array($fatal, self::FATAL_MARKERS, true))
        ) {
            return new self();
        }

        return new self((float) $checkedAt, $etag, $failures, (float) $nextAttempt, $fatal);
    }

    /** save writes the state file atomically (mode 0600). */
    public function save(string $path): void
    {
        $data = [
            'checked_at' => $this->checkedAt,
            'etag' => $this->etag,
            'failures' => $this->failures,
            'next_attempt' => $this->nextAttempt,
            'fatal' => $this->fatal,
        ];
        $json = json_encode($data, JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION | JSON_INVALID_UTF8_SUBSTITUTE | JSON_THROW_ON_ERROR);
        CacheFiles::writeAtomic($path, $json . "\n");
    }

    public function isFatal(): bool
    {
        return $this->fatal !== null;
    }
}
