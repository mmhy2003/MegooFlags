<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Engine;

/**
 * RFC 3339 timestamps as integer epoch milliseconds, computed by hand rather
 * than with DateTime so every SDK accepts and refuses the same strings.
 *
 * @internal
 */
final class Dates
{
    // \A and \z anchor the whole string ($ would match before a newline);
    // without the u modifier [0-9] is ASCII only.
    private const DATE_TIME = '/\A([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt ]([0-9]{2}):([0-9]{2}):([0-9]{2})'
        . '(?:\.([0-9]+))?(?:[Zz]|([+-])([0-9]{2}):([0-9]{2}))\z/';

    private function __construct() {}

    /**
     * parseRfc3339Millis parses YYYY-MM-DD, a T, t or space, hh:mm:ss, an
     * optional fraction (truncated to milliseconds) and a Z, z or +-hh:mm zone.
     * Returns null for anything else or an out-of-range field. Only the whole
     * offset must be under 24 h.
     */
    public static function parseRfc3339Millis(string $text): ?int
    {
        if (preg_match(self::DATE_TIME, $text, $m, PREG_UNMATCHED_AS_NULL) !== 1) {
            return null;
        }
        [$year, $month, $day, $hour, $minute, $second] = array_map('intval', array_slice($m, 1, 6));
        if (!self::isValid($year, $month, $day, $hour, $minute, $second)) {
            return null;
        }
        $offset = 0;
        if ($m[8] !== null) {
            $offset = (int) $m[9] * 60 + (int) $m[10];
            if ($offset >= 24 * 60) {
                return null;
            }
            if ($m[8] === '-') {
                $offset = -$offset;
            }
        }
        $millis = $m[7] !== null ? (int) str_pad(substr($m[7], 0, 3), 3, '0') : 0;
        $secs = self::daysFromCivil($year, $month, $day) * 86400 + $hour * 3600 + $minute * 60 + $second - $offset * 60;

        return $secs * 1000 + $millis;
    }

    /**
     * toMillis is $value as integer epoch milliseconds: an int as is, a float
     * floored (and clamped to the int range), a string parsed as RFC 3339.
     * Anything else, and a non-finite float, is null.
     */
    public static function toMillis(mixed $value): ?int
    {
        if (is_string($value)) {
            return self::parseRfc3339Millis($value);
        }
        if (is_int($value)) {
            return $value;
        }
        if (!is_float($value) || !is_finite($value)) {
            return null;
        }
        $floored = floor($value);
        if ($floored >= (float) PHP_INT_MAX) { // (float) PHP_INT_MAX is 2^63
            return PHP_INT_MAX;
        }
        if ($floored <= (float) PHP_INT_MIN) {
            return PHP_INT_MIN;
        }

        return (int) $floored;
    }

    private static function isValid(int $year, int $month, int $day, int $hour, int $minute, int $second): bool
    {
        return $year >= 1 && $month >= 1 && $month <= 12 && $day >= 1 && $day <= self::daysIn($year, $month)
            && $hour <= 23 && $minute <= 59 && $second <= 59;
    }

    /** daysIn is the number of days in $month of the proleptic Gregorian $year. */
    private static function daysIn(int $year, int $month): int
    {
        return match ($month) {
            2 => ($year % 4 === 0 && ($year % 100 !== 0 || $year % 400 === 0)) ? 29 : 28,
            4, 6, 9, 11 => 30,
            default => 31,
        };
    }

    /**
     * daysFromCivil is the days since 1970-01-01 of a proleptic Gregorian date.
     * The year is at least 1, so every intdiv operand is non-negative and
     * truncation equals the floor division the algorithm needs.
     */
    private static function daysFromCivil(int $year, int $month, int $day): int
    {
        $y = $month <= 2 ? $year - 1 : $year;
        $era = intdiv($y, 400);
        $yoe = $y - $era * 400;
        $doy = intdiv(153 * ($month + ($month > 2 ? -3 : 9)) + 2, 5) + $day - 1;
        $doe = $yoe * 365 + intdiv($yoe, 4) - intdiv($yoe, 100) + $doy;

        return $era * 146097 + $doe - 719468;
    }
}
