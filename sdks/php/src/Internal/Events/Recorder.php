<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Events;

/**
 * Recorder collects the evaluations of one request: a count per flag,
 * variant (null when not served) and UTC hour, and one exposure per context
 * key, flag, variant and hour. take() turns them into one spool line:
 *
 *   {"counts": [[flag, variant or null, hourEpochSeconds, n]...],
 *    "exposures": [{"flag_key", "variant", "reason", "timestamp",
 *                   "context_key", "context"?}...]}
 *
 * At most $maxPending exposures are kept (more are dropped and counted);
 * counts are never dropped.
 *
 * @internal
 */
final class Recorder
{
    public const MAX_PENDING = 10_000;

    /** @var array<string, array{string, ?string, int, int}> */
    private array $counts = [];

    /** @var array<string, true> */
    private array $seen = [];

    /** @var list<array<string, mixed>> */
    private array $exposures = [];

    private int $dropped = 0;

    public function __construct(private readonly bool $sendContext, private readonly int $maxPending = self::MAX_PENDING) {}

    /**
     * record adds one evaluation made at $at (Unix seconds). $contextKey is
     * '' when the context has no string key, so anonymous contexts share
     * one exposure.
     *
     * @param array<string, mixed> $context
     */
    public function record(string $flagKey, ?string $variant, bool $served, string $reason, string $contextKey, array $context, float $at): void
    {
        $hour = intdiv((int) floor($at), 3600) * 3600;
        $variant = $served ? $variant : null;
        $countKey = self::key($flagKey, $variant, $hour);
        if (isset($this->counts[$countKey])) {
            $this->counts[$countKey][3]++;
        } else {
            $this->counts[$countKey] = [$flagKey, $variant, $hour, 1];
        }
        if ($variant === null) {
            return;
        }
        $seenKey = self::key($contextKey, $flagKey, $variant, $hour);
        if (isset($this->seen[$seenKey])) {
            return;
        }
        if (count($this->exposures) >= $this->maxPending) {
            $this->dropped++;

            return;
        }
        $this->seen[$seenKey] = true;
        $exposure = [
            'flag_key' => $flagKey,
            'variant' => $variant,
            'reason' => $reason,
            'timestamp' => self::timestamp($at),
            'context_key' => $contextKey,
        ];
        if ($this->sendContext) {
            $exposure['context'] = (object) $context;
        }
        $this->exposures[] = $exposure;
    }

    /** dropped is the number of exposures dropped because too many were pending. */
    public function dropped(): int
    {
        return $this->dropped;
    }

    /**
     * take returns the spool line (without a newline) and clears the
     * recorder, or null when nothing was recorded.
     */
    public function take(): ?string
    {
        if ($this->counts === []) {
            return null;
        }
        $line = self::encode(['counts' => array_values($this->counts), 'exposures' => $this->exposures]);
        $this->counts = [];
        $this->seen = [];
        $this->exposures = [];

        return $line;
    }

    /** timestamp formats Unix seconds like 2026-11-10T09:15:02.000Z. */
    public static function timestamp(float $at): string
    {
        $seconds = (int) floor($at);
        $millis = min(999, (int) floor(($at - $seconds) * 1000));

        return gmdate('Y-m-d\TH:i:s', $seconds) . sprintf('.%03dZ', $millis);
    }

    /** encode writes spool and body JSON; it never throws (bad values become null). */
    public static function encode(mixed $data): string
    {
        return (string) json_encode(
            $data,
            JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PRESERVE_ZERO_FRACTION | JSON_PARTIAL_OUTPUT_ON_ERROR,
        );
    }

    private static function key(mixed ...$parts): string
    {
        return self::encode($parts);
    }
}
