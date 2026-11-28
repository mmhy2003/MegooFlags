<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Engine;

use JsonException;
use stdClass;

/**
 * Compiler validates a ruleset (evaluation.md §11) and turns it into nested
 * PHP arrays of scalars only, so that var_export of the result is constant
 * data for OPcache. Unknown fields are ignored. Prerequisite flag keys and
 * segment keys are not checked: a missing flag fails the prerequisite and a
 * missing segment is not matched. Cycles are not checked.
 *
 * The tree is JSON as json_decode($text, false) gives it: objects are
 * stdClass, arrays are lists. Keys that look like integers become int array
 * keys in the result; PHP converts lookups the same way, and every key that
 * is also a value (variants, flag keys) is kept as a string value.
 *
 * @internal
 *
 * @phpstan-type Serve array{v: string}|array{by: string, w: list<array{string, int}>}
 * @phpstan-type Clause array{a: string, op: string, n: bool, p: list<mixed>}
 * @phpstan-type Variation array{s: scalar|null}|array{j: string}
 * @phpstan-type Flag array{type: string, enabled: bool, version: int,
 *     variations: array<array-key, Variation>, off: ?string, prereqs: list<array{string, string}>,
 *     targets: list<array{string, array<array-key, true>}>,
 *     rules: list<array{clauses: list<Clause>, serve: Serve}>, fallthrough: Serve}
 * @phpstan-type Segment array{included: array<array-key, true>, excluded: array<array-key, true>,
 *     rules: list<list<Clause>>}
 * @phpstan-type Ruleset array{project: string, environment: string, warnings: list<string>,
 *     flags: array<array-key, Flag>, segments: array<array-key, Segment>}
 */
final class Compiler
{
    public const SCHEMA_VERSION = 1;

    private const FLAG_TYPES = ['boolean', 'string', 'number', 'json'];
    private const OPERATORS = [
        'in', 'not_in', 'contains', 'starts_with', 'ends_with', 'matches', 'lt', 'lte', 'gt', 'gte',
        'semver_eq', 'semver_lt', 'semver_gt', 'before', 'after', 'in_segment',
    ];
    private const JSON_FLAGS = JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRESERVE_ZERO_FRACTION
        | JSON_THROW_ON_ERROR;

    /** @var list<string> */
    private array $warnings = [];

    private function __construct() {}

    /**
     * compileJson decodes $json and compiles it. Invalid JSON is a
     * ParseException with the path "ruleset".
     *
     * @return Ruleset
     */
    public static function compileJson(string $json): array
    {
        try {
            $tree = json_decode($json, false, 512, JSON_THROW_ON_ERROR);
        } catch (JsonException $e) {
            throw new ParseException('ruleset: invalid JSON: ' . $e->getMessage(), 0, $e);
        }

        return self::compile($tree);
    }

    /**
     * compile validates the decoded $tree and returns the compiled ruleset. It
     * throws ParseException naming the bad JSON path. A matches value that does
     * not compile is not an error: it never matches and is named in warnings.
     *
     * @return Ruleset
     */
    public static function compile(mixed $tree): array
    {
        return (new self())->ruleset($tree);
    }

    /** @return Ruleset */
    private function ruleset(mixed $tree): array
    {
        $root = self::object($tree, 'ruleset');
        $version = self::integer(self::field($root, 'schema_version', ''), 'schema_version');
        if ($version !== self::SCHEMA_VERSION) {
            self::fail('schema_version', 'must be ' . self::SCHEMA_VERSION);
        }
        $project = self::string(self::field($root, 'project', ''), 'project');
        $environment = self::string(self::field($root, 'environment', ''), 'environment');
        $flagTree = self::object(self::field($root, 'flags', ''), 'flags');
        $segmentTree = self::object(self::field($root, 'segments', ''), 'segments');

        $flags = [];
        foreach (get_object_vars($flagTree) as $key => $value) {
            $key = (string) $key;
            $flags[$key] = $this->flag($key, $value, "flags.{$key}");
        }
        $segments = [];
        foreach (get_object_vars($segmentTree) as $key => $value) {
            $key = (string) $key;
            $segments[$key] = $this->segment($value, "segments.{$key}");
        }

        return [
            'project' => $project,
            'environment' => $environment,
            'warnings' => $this->warnings,
            'flags' => $flags,
            'segments' => $segments,
        ];
    }

    private static function fail(string $path, string $reason): never
    {
        throw new ParseException("{$path}: {$reason}");
    }

    /** field is $obj->$name, which must be present (null counts as present). */
    private static function field(stdClass $obj, string $name, string $path): mixed
    {
        if (!property_exists($obj, $name)) {
            self::fail($path === '' ? $name : "{$path}.{$name}", 'is required');
        }

        return $obj->{$name};
    }

    private static function object(mixed $value, string $path): stdClass
    {
        if (!$value instanceof stdClass) {
            self::fail($path, 'must be an object');
        }

        return $value;
    }

    /** @return list<mixed> */
    private static function list(mixed $value, string $path, bool $nonEmpty = false): array
    {
        if (!is_array($value) || !array_is_list($value)) {
            self::fail($path, 'must be an array');
        }
        if ($nonEmpty && $value === []) {
            self::fail($path, 'must not be empty');
        }

        return $value;
    }

    private static function string(mixed $value, string $path): string
    {
        if (!is_string($value)) {
            self::fail($path, 'must be a string');
        }

        return $value;
    }

    private static function boolean(mixed $value, string $path): bool
    {
        if (!is_bool($value)) {
            self::fail($path, 'must be a boolean');
        }

        return $value;
    }

    /** integer refuses true, false and floats such as 1.0. */
    private static function integer(mixed $value, string $path): int
    {
        if (!is_int($value)) {
            self::fail($path, 'must be an integer');
        }

        return $value;
    }

    /** @return array<array-key, true> the strings of a list, as set keys */
    private static function stringSet(mixed $value, string $path): array
    {
        $set = [];
        foreach (self::list($value, $path) as $i => $item) {
            $set[self::string($item, "{$path}[{$i}]")] = true;
        }

        return $set;
    }

    /** @param array<array-key, mixed> $variations */
    private static function known(mixed $value, array $variations, string $path): string
    {
        $key = self::string($value, $path);
        if (!array_key_exists($key, $variations)) {
            self::fail($path, "unknown variation '{$key}'");
        }

        return $key;
    }

    /** @return Flag */
    private function flag(string $key, mixed $value, string $path): array
    {
        $obj = self::object($value, $path);
        if (self::field($obj, 'key', $path) !== $key) {
            self::fail("{$path}.key", "must equal '{$key}'");
        }
        $type = self::string(self::field($obj, 'type', $path), "{$path}.type");
        if (!in_array($type, self::FLAG_TYPES, true)) {
            self::fail("{$path}.type", 'must be one of ' . implode(', ', self::FLAG_TYPES));
        }
        $variations = self::variations(self::field($obj, 'variations', $path), "{$path}.variations");
        $off = self::field($obj, 'off_variation', $path);

        $prereqs = [];
        foreach (self::list(self::field($obj, 'prerequisites', $path), "{$path}.prerequisites") as $i => $item) {
            $at = "{$path}.prerequisites[{$i}]";
            $p = self::object($item, $at);
            $prereqs[] = [
                self::string(self::field($p, 'flag', $at), "{$at}.flag"),
                self::string(self::field($p, 'variation', $at), "{$at}.variation"),
            ];
        }
        $targets = [];
        foreach (self::list(self::field($obj, 'targets', $path), "{$path}.targets") as $i => $item) {
            $at = "{$path}.targets[{$i}]";
            $t = self::object($item, $at);
            $targets[] = [
                self::known(self::field($t, 'variation', $at), $variations, "{$at}.variation"),
                self::stringSet(self::field($t, 'values', $at), "{$at}.values"),
            ];
        }
        $rules = [];
        foreach (self::list(self::field($obj, 'rules', $path), "{$path}.rules") as $i => $item) {
            $at = "{$path}.rules[{$i}]";
            $r = self::object($item, $at);
            if (property_exists($r, 'description')) {
                self::string($r->description, "{$at}.description");
            }
            $rules[] = [
                'clauses' => $this->clauses(self::field($r, 'clauses', $at), "{$at}.clauses", false),
                'serve' => self::serve(self::field($r, 'serve', $at), $variations, "{$at}.serve"),
            ];
        }

        return [
            'type' => $type,
            'enabled' => self::boolean(self::field($obj, 'enabled', $path), "{$path}.enabled"),
            'version' => self::integer(self::field($obj, 'version', $path), "{$path}.version"),
            'variations' => $variations,
            'off' => $off === null ? null : self::known($off, $variations, "{$path}.off_variation"),
            'prereqs' => $prereqs,
            'targets' => $targets,
            'rules' => $rules,
            'fallthrough' => self::serve(self::field($obj, 'fallthrough', $path), $variations, "{$path}.fallthrough"),
        ];
    }

    /**
     * variations keeps a scalar or null value as ['s' => value] and an array
     * or object as ['j' => its JSON text], decoded when it is served.
     *
     * @return array<array-key, Variation>
     */
    private static function variations(mixed $value, string $path): array
    {
        $variations = [];
        foreach (self::list($value, $path, true) as $i => $item) {
            $at = "{$path}[{$i}]";
            $obj = self::object($item, $at);
            $key = self::string(self::field($obj, 'key', $at), "{$at}.key");
            if (array_key_exists($key, $variations)) {
                self::fail("{$at}.key", "duplicate variation '{$key}'");
            }
            $raw = self::field($obj, 'value', $at);
            $variations[$key] = is_array($raw) || is_object($raw)
                ? ['j' => json_encode($raw, self::JSON_FLAGS)]
                : ['s' => $raw];
        }

        return $variations;
    }

    /**
     * @param array<array-key, mixed> $variations
     * @return Serve
     */
    private static function serve(mixed $value, array $variations, string $path): array
    {
        $obj = self::object($value, $path);
        $hasVariation = property_exists($obj, 'variation');
        if ($hasVariation === property_exists($obj, 'rollout')) {
            self::fail($path, 'must have exactly one of variation or rollout');
        }
        if ($hasVariation) {
            return ['v' => self::known($obj->variation, $variations, "{$path}.variation")];
        }

        $at = "{$path}.rollout";
        $rollout = self::object($obj->rollout, $at);
        $bucketBy = self::string(self::field($rollout, 'bucket_by', $at), "{$at}.bucket_by");
        $weights = [];
        $total = 0;
        foreach (self::list(self::field($rollout, 'weights', $at), "{$at}.weights", true) as $i => $item) {
            $wat = "{$at}.weights[{$i}]";
            $w = self::object($item, $wat);
            $variation = self::known(self::field($w, 'variation', $wat), $variations, "{$wat}.variation");
            $weight = self::integer(self::field($w, 'weight', $wat), "{$wat}.weight");
            if ($weight < 0 || $weight > Bucketing::BUCKETS) {
                self::fail("{$wat}.weight", 'must be between 0 and ' . Bucketing::BUCKETS);
            }
            $weights[] = [$variation, $weight];
            $total += $weight;
        }
        if ($total !== Bucketing::BUCKETS) {
            self::fail("{$at}.weights", 'must add up to ' . Bucketing::BUCKETS);
        }

        return ['by' => $bucketBy, 'w' => $weights];
    }

    /** @return list<Clause> */
    private function clauses(mixed $value, string $path, bool $inSegment): array
    {
        $clauses = [];
        foreach (self::list($value, $path, true) as $i => $item) {
            $clauses[] = $this->clause($item, "{$path}[{$i}]", $inSegment);
        }

        return $clauses;
    }

    /** @return Clause */
    private function clause(mixed $value, string $path, bool $inSegment): array
    {
        $obj = self::object($value, $path);
        $attribute = self::string(self::field($obj, 'attribute', $path), "{$path}.attribute");
        $operator = self::string(self::field($obj, 'operator', $path), "{$path}.operator");
        if (!in_array($operator, self::OPERATORS, true)) {
            self::fail("{$path}.operator", "unknown operator '{$operator}'");
        }
        if ($inSegment && $operator === 'in_segment') {
            self::fail("{$path}.operator", 'in_segment is not allowed in a segment rule');
        }
        $values = self::list(self::field($obj, 'values', $path), "{$path}.values", true);
        foreach ($values as $i => $item) {
            if (!Values::isScalar($item)) {
                self::fail("{$path}.values[{$i}]", 'must be a string, number or boolean');
            }
        }

        return [
            'a' => $attribute,
            'op' => $operator,
            'n' => property_exists($obj, 'negate') ? self::boolean($obj->negate, "{$path}.negate") : false,
            'p' => $this->prepare($operator, $values, "{$path}.values"),
        ];
    }

    /**
     * prepare is the clause values the operator can use, in the form it
     * needs. A value it can't use is left out, so it never matches.
     *
     * @param list<mixed> $values
     * @return list<mixed>
     */
    private function prepare(string $operator, array $values, string $path): array
    {
        $prepared = [];
        foreach ($values as $i => $v) {
            $p = match ($operator) {
                'in', 'not_in' => $v,
                'contains', 'starts_with', 'ends_with', 'in_segment' => is_string($v) ? $v : null,
                'lt', 'lte', 'gt', 'gte' => Values::isNumber($v) ? $v : null,
                'semver_eq', 'semver_lt', 'semver_gt' => is_string($v) ? Semver::parse($v) : null,
                'before', 'after' => is_string($v) ? Dates::parseRfc3339Millis($v) : null,
                default => is_string($v) ? $this->pattern($v, "{$path}[{$i}]") : null, // matches
            };
            if ($p !== null) {
                $prepared[] = $p;
            }
        }

        return $prepared;
    }

    private function pattern(string $pattern, string $path): ?string
    {
        $pcre = Regex::translate($pattern, $error);
        if ($pcre === null) {
            $this->warnings[] = "{$path}: regex does not compile: {$error}";
        }

        return $pcre;
    }

    /** @return Segment */
    private function segment(mixed $value, string $path): array
    {
        $obj = self::object($value, $path);
        $included = self::stringSet(self::field($obj, 'included', $path), "{$path}.included");
        $excluded = self::stringSet(self::field($obj, 'excluded', $path), "{$path}.excluded");
        $rules = [];
        foreach (self::list(self::field($obj, 'rules', $path), "{$path}.rules") as $i => $item) {
            $at = "{$path}.rules[{$i}]";
            $rules[] = $this->clauses(self::field(self::object($item, $at), 'clauses', $at), "{$at}.clauses", true);
        }

        return ['included' => $included, 'excluded' => $excluded, 'rules' => $rules];
    }
}
