<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Engine;

/**
 * JSON values as the engine sees them, decoded with json_decode($text, false):
 * null, bool, int, float, string, list and stdClass. A bool is never a number.
 *
 * @internal
 */
final class Values
{
    private function __construct() {}

    /** isNumber reports whether $value is an int or a float. */
    public static function isNumber(mixed $value): bool
    {
        return is_int($value) || is_float($value);
    }

    /** isScalar reports whether $value is a string, a bool or a number. */
    public static function isScalar(mixed $value): bool
    {
        return is_string($value) || is_bool($value) || self::isNumber($value);
    }

    /**
     * jsonEqual is JSON scalar equality: a bool only equals a bool, two ints
     * compare exactly, other numbers as floats, a string equals a string.
     * Anything else, null included, is never equal.
     */
    public static function jsonEqual(mixed $left, mixed $right): bool
    {
        if (is_bool($left) || is_bool($right)) {
            return $left === $right;
        }
        if (is_string($left) && is_string($right)) {
            return $left === $right;
        }
        if (!self::isNumber($left) || !self::isNumber($right)) {
            return false;
        }
        if (is_int($left) && is_int($right)) {
            return $left === $right;
        }

        return (float) $left === (float) $right;
    }
}
