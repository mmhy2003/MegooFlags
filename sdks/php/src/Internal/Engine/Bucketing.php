<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Engine;

/**
 * Rollout bucketing: sha256 of "<flag key>.<text>" into 100 000 buckets.
 *
 * @internal
 */
final class Bucketing
{
    /** BUCKETS is the number of rollout buckets; weights add up to it. */
    public const BUCKETS = 100000;

    /** MAX_SAFE is 2^53 - 1, the largest integer every SDK buckets the same way. */
    public const MAX_SAFE = 9007199254740991;

    private function __construct() {}

    /**
     * bucketText is the text hashed for $value, or null when it can't be
     * bucketed (which counts as a missing attribute). A string is used as is,
     * an integral number with abs <= 2^53-1 as its base-10 text.
     */
    public static function bucketText(mixed $value): ?string
    {
        if (is_string($value)) {
            return $value;
        }
        if (is_int($value)) {
            return $value >= -self::MAX_SAFE && $value <= self::MAX_SAFE ? (string) $value : null;
        }
        if (is_float($value)) {
            if (!is_finite($value) || $value !== floor($value) || abs($value) > self::MAX_SAFE) {
                return null;
            }

            return (string) (int) $value; // (int) -0.0 is 0
        }

        return null;
    }

    /**
     * bucket is uint64_be(sha256($flagKey . "." . $text)[0, 8]) mod BUCKETS,
     * reduced byte by byte as PHP has no unsigned 64-bit integer.
     */
    public static function bucket(string $flagKey, string $text): int
    {
        $sum = hash('sha256', $flagKey . '.' . $text, true);
        $r = 0;
        for ($i = 0; $i < 8; $i++) {
            $r = ($r * 256 + ord($sum[$i])) % self::BUCKETS;
        }

        return $r;
    }

    /**
     * pickVariation returns the first variation whose running total of weights
     * is greater than $bucket. The compiler makes the weights add up to BUCKETS.
     *
     * @param list<array{string, int}> $weights [variation, weight] pairs
     */
    public static function pickVariation(array $weights, int $bucket): ?string
    {
        $total = 0;
        foreach ($weights as [$variation, $weight]) {
            $total += $weight;
            if ($total > $bucket) {
                return $variation;
            }
        }

        return null;
    }
}
