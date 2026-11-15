<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Transport;

/**
 * Backoff computes jittered exponential waits from a failure count, so the
 * count can live in the state file between requests.
 *
 * @internal
 */
final class Backoff
{
    /**
     * delay is the wait in seconds after $failures failures in a row: none
     * for 0, min(base, max) for the first, then min(max, base * 2^(n-1))
     * scaled by 0.8 + 0.2 * $rand, where $rand is in [0, 1].
     */
    public static function delay(int $failures, float $base, float $max, float $rand): float
    {
        if ($failures <= 0) {
            return 0.0;
        }
        if ($failures === 1) {
            return min($base, $max);
        }
        // Double step by step so a large count cannot overflow.
        $wait = $base;
        for ($i = 1; $i < $failures && $wait < $max; $i++) {
            $wait *= 2;
        }

        return min($wait, $max) * (0.8 + 0.2 * $rand);
    }
}
