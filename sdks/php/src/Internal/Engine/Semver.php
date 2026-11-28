<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Engine;

/**
 * Strict SemVer 2.0.0 parsing and precedence.
 *
 * A parsed version is [core, pre]: core holds three digit strings without
 * leading zeros, so they compare by (length, text) without overflow; pre is a
 * list of [numeric, text] identifiers, or null when there is no prerelease.
 * Build metadata is dropped, as it has no precedence. Plain arrays keep a
 * parsed version usable as constant data in the compiled ruleset.
 *
 * @internal
 *
 * @phpstan-type PreId array{bool, string}
 * @phpstan-type Version array{list<string>, list<PreId>|null}
 */
final class Semver
{
    // \A and \z anchor the whole string ($ would match before a newline);
    // without the u modifier the classes are ASCII only.
    private const DIGITS = '/\A[0-9]+\z/';
    private const IDENT = '/\A[0-9A-Za-z-]+\z/';

    private function __construct() {}

    /**
     * parse parses a strict SemVer 2.0.0 string: ASCII only, no "v" prefix, no
     * surrounding whitespace, no leading zeros in numeric parts. Returns null
     * for anything else.
     *
     * @return Version|null
     */
    public static function parse(string $text): ?array
    {
        $parts = explode('+', $text, 2);
        if (count($parts) === 2 && self::identifiers($parts[1]) === null) {
            return null;
        }
        $parts = explode('-', $parts[0], 2);
        $pre = null;
        if (count($parts) === 2) {
            $pre = self::parsePre($parts[1]);
            if ($pre === null) {
                return null;
            }
        }
        $core = explode('.', $parts[0]);
        if (count($core) !== 3) {
            return null;
        }
        foreach ($core as $part) {
            if (!self::isNumericPart($part)) {
                return null;
            }
        }

        return [$core, $pre];
    }

    /**
     * compare returns -1, 0 or 1 by SemVer 2.0.0 precedence.
     *
     * @param Version $left
     * @param Version $right
     */
    public static function compare(array $left, array $right): int
    {
        for ($i = 0; $i < 3; $i++) {
            $c = self::compareDigits($left[0][$i], $right[0][$i]);
            if ($c !== 0) {
                return $c;
            }
        }
        [, $leftPre] = $left;
        [, $rightPre] = $right;
        if ($leftPre === null) {
            return $rightPre === null ? 0 : 1;
        }
        if ($rightPre === null) {
            return -1;
        }
        $n = min(count($leftPre), count($rightPre));
        for ($i = 0; $i < $n; $i++) {
            $c = self::compareIds($leftPre[$i], $rightPre[$i]);
            if ($c !== 0) {
                return $c;
            }
        }

        return count($leftPre) <=> count($rightPre);
    }

    /**
     * identifiers splits $text on "." and returns the parts when each is a
     * non-empty [0-9A-Za-z-] run, else null.
     *
     * @return list<string>|null
     */
    private static function identifiers(string $text): ?array
    {
        $parts = explode('.', $text);
        foreach ($parts as $id) {
            if (preg_match(self::IDENT, $id) !== 1) {
                return null;
            }
        }

        return $parts;
    }

    /** @return list<PreId>|null */
    private static function parsePre(string $text): ?array
    {
        $ids = self::identifiers($text);
        if ($ids === null) {
            return null;
        }
        $pre = [];
        foreach ($ids as $id) {
            $numeric = preg_match(self::DIGITS, $id) === 1;
            if ($numeric && !self::isNumericPart($id)) {
                return null;
            }
            $pre[] = [$numeric, $id];
        }

        return $pre;
    }

    /**
     * @param PreId $left
     * @param PreId $right
     */
    private static function compareIds(array $left, array $right): int
    {
        [$leftNumeric, $leftText] = $left;
        [$rightNumeric, $rightText] = $right;
        if ($leftNumeric && $rightNumeric) {
            return self::compareDigits($leftText, $rightText);
        }
        if ($leftNumeric) {
            return -1;
        }
        if ($rightNumeric) {
            return 1;
        }

        return strcmp($leftText, $rightText) <=> 0;
    }

    /** compareDigits compares two digit strings without leading zeros. */
    private static function compareDigits(string $left, string $right): int
    {
        if (strlen($left) !== strlen($right)) {
            return strlen($left) <=> strlen($right);
        }

        return strcmp($left, $right) <=> 0;
    }

    /** isNumericPart reports whether $text is "0" or [1-9][0-9]*. */
    private static function isNumericPart(string $text): bool
    {
        return preg_match(self::DIGITS, $text) === 1 && ($text === '0' || $text[0] !== '0');
    }
}
