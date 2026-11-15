<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Engine;

use InvalidArgumentException;
use OverflowException;
use stdClass;

/**
 * Evaluator resolves a flag of a compiled ruleset for a context as
 * spec/evaluation.md says. It is pure: the compiled ruleset is plain data.
 *
 * The context is a stdClass of attributes. An attribute that is a PHP list is
 * a JSON array; a stdClass or an array that is not a list is a JSON object,
 * which never matches an operator.
 *
 * @internal
 *
 * @phpstan-import-type Ruleset from Compiler
 * @phpstan-import-type Flag from Compiler
 * @phpstan-import-type Clause from Compiler
 * @phpstan-import-type Serve from Compiler
 * @phpstan-import-type Segment from Compiler
 */
final class Evaluator
{
    public const MAX_PREREQUISITE_DEPTH = 32;

    private const EXPECTED_TYPES = ['boolean', 'string', 'number', 'json'];
    private const SEMVER_WANT = ['semver_eq' => 0, 'semver_lt' => -1, 'semver_gt' => 1];

    /**
     * @param bool $assoc whether a served array or object value is decoded to
     *                    PHP arrays (true) or to lists and stdClass (false)
     */
    public function __construct(private readonly bool $assoc) {}

    /**
     * evaluate resolves $flagKey for $context. $expectedType is "boolean",
     * "string", "number", "json" or null, and is checked only when a variation
     * was served. Errors return $default with reason ERROR.
     *
     * @param Ruleset $ruleset
     */
    public function evaluate(array $ruleset, string $flagKey, mixed $context, mixed $default, ?string $expectedType): Result
    {
        if ($expectedType !== null && !in_array($expectedType, self::EXPECTED_TYPES, true)) {
            throw new InvalidArgumentException("unknown flag type: {$expectedType}");
        }
        if (!$context instanceof stdClass) {
            return self::error($default, 'INVALID_CONTEXT', 'context must be a JSON object');
        }
        $flag = $ruleset['flags'][$flagKey] ?? null;
        if ($flag === null) {
            return self::error($default, 'FLAG_NOT_FOUND', "flag '{$flagKey}' not found");
        }

        try {
            $result = $this->evaluateFlag($ruleset, $flagKey, $flag, get_object_vars($context), $default, 0);
        } catch (OverflowException) {
            return self::error($default, 'GENERAL', 'prerequisite chain is too deep or has a cycle');
        }
        if ($expectedType !== null && $result->variant !== null && !self::isType($result->value, $expectedType)) {
            return self::error($default, 'TYPE_MISMATCH', "flag '{$flagKey}' is not a {$expectedType}");
        }

        return $result;
    }

    /**
     * @param Ruleset $ruleset
     * @param Flag $flag
     * @param array<array-key, mixed> $attrs
     */
    private function evaluateFlag(array $ruleset, string $flagKey, array $flag, array $attrs, mixed $default, int $depth): Result
    {
        if (!$flag['enabled']) {
            return $this->serve($flag, $flag['off'], 'DISABLED', $default);
        }
        foreach ($flag['prereqs'] as [$prereqKey, $variation]) {
            if (!$this->prerequisiteMet($ruleset, $prereqKey, $variation, $attrs, $depth + 1)) {
                return $this->serve($flag, $flag['off'], 'PREREQUISITE_FAILED', $default, null, $prereqKey);
            }
        }
        $key = $attrs['key'] ?? null;
        if (is_string($key)) {
            foreach ($flag['targets'] as [$variation, $values]) {
                if (isset($values[$key])) {
                    return $this->serve($flag, $variation, 'TARGETING_MATCH', $default);
                }
            }
        }
        foreach ($flag['rules'] as $index => $rule) {
            if (self::allMatch($ruleset, $rule['clauses'], $attrs)) {
                return $this->serveRule($flagKey, $flag, $rule['serve'], $attrs, 'TARGETING_MATCH', $default, $index);
            }
        }

        return $this->serveRule($flagKey, $flag, $flag['fallthrough'], $attrs, self::fallthroughReason($flag), $default, null);
    }

    /** @param Flag $flag */
    private static function fallthroughReason(array $flag): string
    {
        if (isset($flag['fallthrough']['w'])) {
            return 'SPLIT';
        }

        return $flag['targets'] === [] && $flag['rules'] === [] && $flag['prereqs'] === [] ? 'STATIC' : 'DEFAULT';
    }

    /**
     * @param Ruleset $ruleset
     * @param array<array-key, mixed> $attrs
     */
    private function prerequisiteMet(array $ruleset, string $flagKey, string $variation, array $attrs, int $depth): bool
    {
        if ($depth > self::MAX_PREREQUISITE_DEPTH) {
            throw new OverflowException('prerequisite depth');
        }
        $flag = $ruleset['flags'][$flagKey] ?? null;
        if ($flag === null || !$flag['enabled']) {
            return false;
        }
        $result = $this->evaluateFlag($ruleset, $flagKey, $flag, $attrs, null, $depth);

        return $result->reason !== 'ERROR' && $result->variant === $variation;
    }

    /**
     * @param Ruleset $ruleset
     * @param list<Clause> $clauses
     * @param array<array-key, mixed> $attrs
     */
    private static function allMatch(array $ruleset, array $clauses, array $attrs): bool
    {
        foreach ($clauses as $clause) {
            if (!self::clauseMatches($ruleset, $clause, $attrs)) {
                return false;
            }
        }

        return true;
    }

    /**
     * @param Ruleset $ruleset
     * @param Clause $clause
     * @param array<array-key, mixed> $attrs
     */
    private static function clauseMatches(array $ruleset, array $clause, array $attrs): bool
    {
        if ($clause['op'] === 'in_segment') {
            $hit = false;
            foreach ($clause['p'] as $segmentKey) {
                $segment = $ruleset['segments'][$segmentKey] ?? null;
                if ($segment !== null && self::inSegment($ruleset, $segment, $attrs)) {
                    $hit = true;
                    break;
                }
            }

            return $hit !== $clause['n'];
        }
        $value = $attrs[$clause['a']] ?? null;
        if ($value === null) {
            return false; // missing or null: false, negate not applied
        }
        $hit = false;
        if (is_array($value) && array_is_list($value)) {
            foreach ($value as $element) {
                if (self::matchValue($clause['op'], $element, $clause['p'])) {
                    $hit = true;
                    break;
                }
            }
        } else {
            $hit = self::matchValue($clause['op'], $value, $clause['p']);
        }

        return $hit !== $clause['n'];
    }

    /**
     * @param Ruleset $ruleset
     * @param Segment $segment
     * @param array<array-key, mixed> $attrs
     */
    private static function inSegment(array $ruleset, array $segment, array $attrs): bool
    {
        $key = $attrs['key'] ?? null;
        if (is_string($key)) {
            if (isset($segment['excluded'][$key])) {
                return false;
            }
            if (isset($segment['included'][$key])) {
                return true;
            }
        }
        foreach ($segment['rules'] as $clauses) {
            if (self::allMatch($ruleset, $clauses, $attrs)) {
                return true;
            }
        }

        return false;
    }

    /**
     * matchValue reports whether one element of the attribute matches the
     * clause's prepared values. An element of the wrong type, arrays and
     * objects included, never matches.
     *
     * @param list<mixed> $prepared
     */
    private static function matchValue(string $operator, mixed $element, array $prepared): bool
    {
        switch ($operator) {
            case 'in':
                foreach ($prepared as $p) {
                    if (Values::jsonEqual($element, $p)) {
                        return true;
                    }
                }

                return false;
            case 'not_in':
                if (!Values::isScalar($element)) {
                    return false;
                }
                foreach ($prepared as $p) {
                    if (Values::jsonEqual($element, $p)) {
                        return false;
                    }
                }

                return true;
            case 'contains':
            case 'starts_with':
            case 'ends_with':
            case 'matches':
                return is_string($element) && self::stringMatch($operator, $element, $prepared);
            case 'lt':
            case 'lte':
            case 'gt':
            case 'gte':
                return Values::isNumber($element) && self::numericMatch($operator, $element, $prepared);
            case 'semver_eq':
            case 'semver_lt':
            case 'semver_gt':
                return is_string($element) && self::semverMatch($operator, $element, $prepared);
            case 'before':
            case 'after':
                return self::dateMatch($operator, $element, $prepared);
            default:
                return false;
        }
    }

    /**
     * Byte-wise search on UTF-8 is search by code point, as UTF-8 is
     * self-synchronising.
     *
     * @param list<mixed> $prepared
     */
    private static function stringMatch(string $operator, string $element, array $prepared): bool
    {
        foreach ($prepared as $p) {
            /** @var string $p */
            $hit = match ($operator) {
                'contains' => str_contains($element, $p),
                'starts_with' => str_starts_with($element, $p),
                'ends_with' => str_ends_with($element, $p),
                default => Regex::test($p, $element),
            };
            if ($hit) {
                return true;
            }
        }

        return false;
    }

    /** @param list<mixed> $prepared */
    private static function numericMatch(string $operator, int|float $element, array $prepared): bool
    {
        foreach ($prepared as $p) {
            /** @var int|float $p */
            $cmp = is_int($element) && is_int($p) ? $element <=> $p : (float) $element <=> (float) $p;
            $hit = match ($operator) {
                'lt' => $cmp < 0,
                'lte' => $cmp <= 0,
                'gt' => $cmp > 0,
                default => $cmp >= 0,
            };
            if ($hit) {
                return true;
            }
        }

        return false;
    }

    /** @param list<mixed> $prepared */
    private static function semverMatch(string $operator, string $element, array $prepared): bool
    {
        $version = Semver::parse($element);
        if ($version === null) {
            return false;
        }
        $want = self::SEMVER_WANT[$operator];
        foreach ($prepared as $p) {
            /** @var array{list<string>, list<array{bool, string}>|null} $p */
            if (Semver::compare($version, $p) === $want) {
                return true;
            }
        }

        return false;
    }

    /** @param list<mixed> $prepared */
    private static function dateMatch(string $operator, mixed $element, array $prepared): bool
    {
        $millis = Dates::toMillis($element);
        if ($millis === null) {
            return false;
        }
        foreach ($prepared as $p) {
            if ($operator === 'before' ? $millis < $p : $millis > $p) {
                return true;
            }
        }

        return false;
    }

    private static function isType(mixed $value, string $expectedType): bool
    {
        return match ($expectedType) {
            'boolean' => is_bool($value),
            'string' => is_string($value),
            'number' => is_int($value) || is_float($value),
            default => true,
        };
    }

    /** @param Flag $flag */
    private function serve(array $flag, ?string $variation, string $reason, mixed $default, ?int $ruleIndex = null, ?string $prerequisite = null): Result
    {
        $value = $default;
        if ($variation !== null) {
            $stored = $flag['variations'][$variation];
            $value = array_key_exists('s', $stored)
                ? $stored['s']
                : json_decode($stored['j'], $this->assoc, 512, JSON_THROW_ON_ERROR);
        }

        return new Result($value, $variation, $reason, null, null, $ruleIndex, $prerequisite);
    }

    /**
     * @param Flag $flag
     * @param Serve $how
     * @param array<array-key, mixed> $attrs
     */
    private function serveRule(string $flagKey, array $flag, array $how, array $attrs, string $reason, mixed $default, ?int $ruleIndex): Result
    {
        if (isset($how['v'])) {
            return $this->serve($flag, $how['v'], $reason, $default, $ruleIndex);
        }
        $text = Bucketing::bucketText($attrs[$how['by']] ?? null);
        $bucket = $text === null ? 0 : Bucketing::bucket($flagKey, $text);

        return $this->serve($flag, Bucketing::pickVariation($how['w'], $bucket), $reason, $default, $ruleIndex);
    }

    private static function error(mixed $default, string $code, string $message): Result
    {
        return new Result($default, null, 'ERROR', $code, $message);
    }
}
