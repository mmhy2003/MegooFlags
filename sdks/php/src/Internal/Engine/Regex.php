<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Engine;

use InvalidArgumentException;

/**
 * Portable regex translator: the subset common to RE2 and Python re, with
 * Python re.ASCII semantics, rewritten into explicit PCRE constructs so that
 * no host default matters. Three stages: parse (recursive descent over code
 * points), an AST whose sets are sorted merged code point ranges, and the
 * emitter. The translated pattern is '~(*UTF)' . source . '~' and never has
 * the modifiers i, m, s or u; the source never holds ^ or $.
 *
 * AST nodes are arrays keyed by 'k': set (r: ranges), concat and alt
 * (items), group (child), repeat (child, min, max or null, lazy) and assert
 * (kind: start, end_dollar, line_start, line_end, word_b, not_word_b).
 *
 * @internal
 *
 * @phpstan-type Range array{int, int}
 * @phpstan-type Flags array{i: bool, m: bool, s: bool}
 * @phpstan-type Node array<string, mixed>
 * @phpstan-type Quantifier array{min: int, max: ?int, end: int}
 */
final class Regex
{
    private const MAX_CP = 0x10FFFF;
    private const MAX_REPEAT = 1000;
    private const PREFIX = '~(*UTF)';
    private const SUFFIX = '~';

    private const DIGIT = [[0x30, 0x39]];
    private const WORD = [[0x30, 0x39], [0x41, 0x5A], [0x5F, 0x5F], [0x61, 0x7A]];
    private const SPACE = [[0x09, 0x0D], [0x20, 0x20]];

    private const CONTROL_ESCAPES = ['t' => 0x09, 'n' => 0x0A, 'r' => 0x0D, 'f' => 0x0C, 'v' => 0x0B, 'a' => 0x07];
    private const ASSERT_ESCAPES = ['A' => 'start', 'b' => 'word_b', 'B' => 'not_word_b'];
    private const FLAG_NAMES = [0x69 => 'i', 0x6D => 'm', 0x73 => 's'];

    /** @var list<int> */
    private array $cps;
    private int $pos = 0;
    /** @var array<string, true> */
    private array $names = [];

    /** @param list<int> $cps */
    private function __construct(array $cps)
    {
        $this->cps = $cps;
    }

    /**
     * translate compiles $pattern with the portable semantics of
     * evaluation.md (Python re with re.ASCII). It returns the complete PCRE
     * pattern, or null with the reason in $error for a pattern outside the
     * portable subset, one that is not valid UTF-8, or one PCRE refused.
     */
    public static function translate(string $pattern, ?string &$error = null): ?string
    {
        $error = null;
        $chars = preg_split('//u', $pattern, -1, PREG_SPLIT_NO_EMPTY);
        if ($chars === false) {
            $error = 'pattern is not valid UTF-8';

            return null;
        }

        try {
            $node = (new self(array_map(self::codePoint(...), $chars)))->parse();
        } catch (InvalidArgumentException $e) {
            $error = $e->getMessage();

            return null;
        }

        $pcre = self::PREFIX . self::emit($node) . self::SUFFIX;
        $warning = null;
        set_error_handler(static function (int $errno, string $message) use (&$warning): bool {
            $warning = $message;

            return true;
        });
        try {
            $result = preg_match($pcre, '');
        } finally {
            restore_error_handler();
        }
        if ($result === false) {
            $error = 'host regex error: ' . ($warning ?? preg_last_error_msg());

            return null;
        }

        return $pcre;
    }

    /**
     * test is an unanchored search of a translated pattern in $subject. A
     * subject that is not valid UTF-8 never matches.
     */
    public static function test(string $pcre, string $subject): bool
    {
        return preg_match('//u', $subject) === 1 && preg_match($pcre, $subject) === 1;
    }

    /** codePoint decodes one valid UTF-8 character. */
    private static function codePoint(string $char): int
    {
        $b = array_values(unpack('C*', $char) ?: []);

        return match (count($b)) {
            1 => $b[0],
            2 => (($b[0] & 0x1F) << 6) | ($b[1] & 0x3F),
            3 => (($b[0] & 0x0F) << 12) | (($b[1] & 0x3F) << 6) | ($b[2] & 0x3F),
            default => (($b[0] & 0x07) << 18) | (($b[1] & 0x3F) << 12) | (($b[2] & 0x3F) << 6) | ($b[3] & 0x3F),
        };
    }

    // ---- Sets: sorted, merged, inclusive code point ranges ----

    /**
     * @param list<Range> $ranges
     * @return list<Range>
     */
    private static function normalize(array $ranges): array
    {
        usort($ranges, static fn(array $a, array $b): int => $a[0] <=> $b[0]);
        $out = [];
        foreach ($ranges as [$lo, $hi]) {
            $last = count($out) - 1;
            if ($last >= 0 && $lo <= $out[$last][1] + 1) {
                if ($hi > $out[$last][1]) {
                    $out[$last][1] = $hi;
                }
            } else {
                $out[] = [$lo, $hi];
            }
        }

        return $out;
    }

    /**
     * @param list<Range> $ranges
     * @return list<Range>
     */
    private static function complement(array $ranges): array
    {
        $out = [];
        $next = 0;
        foreach ($ranges as [$lo, $hi]) {
            if ($lo > $next) {
                $out[] = [$next, $lo - 1];
            }
            $next = $hi + 1;
        }
        if ($next <= self::MAX_CP) {
            $out[] = [$next, self::MAX_CP];
        }

        return $out;
    }

    /**
     * foldAscii adds the other case of every ASCII letter in the set.
     *
     * @param list<Range> $ranges
     * @return list<Range>
     */
    private static function foldAscii(array $ranges): array
    {
        $extra = [];
        foreach ($ranges as [$lo, $hi]) {
            foreach ([[0x41, 0x5A, 0x20], [0x61, 0x7A, -0x20]] as [$a, $z, $shift]) {
                $l = max($lo, $a);
                $h = min($hi, $z);
                if ($l <= $h) {
                    $extra[] = [$l + $shift, $h + $shift];
                }
            }
        }

        return self::normalize([...$ranges, ...$extra]);
    }

    /**
     * withoutSurrogates removes U+D800 to U+DFFF: PHP strings in UTF-8 cannot
     * hold them, and PCRE refuses them in a UTF pattern.
     *
     * @param list<Range> $ranges
     * @return list<Range>
     */
    private static function withoutSurrogates(array $ranges): array
    {
        $out = [];
        foreach ($ranges as [$lo, $hi]) {
            if ($hi < 0xD800 || $lo > 0xDFFF) {
                $out[] = [$lo, $hi];
                continue;
            }
            if ($lo < 0xD800) {
                $out[] = [$lo, 0xD7FF];
            }
            if ($hi > 0xDFFF) {
                $out[] = [0xE000, $hi];
            }
        }

        return $out;
    }

    /** @return list<Range>|null */
    private static function shorthand(?int $code): ?array
    {
        return match ($code) {
            0x64 => self::DIGIT,
            0x44 => self::complement(self::DIGIT),
            0x77 => self::WORD,
            0x57 => self::complement(self::WORD),
            0x73 => self::SPACE,
            0x53 => self::complement(self::SPACE),
            default => null,
        };
    }

    // ---- Stage 1: parse ----

    /** @return Node */
    private function parse(): array
    {
        $flags = ['i' => false, 'm' => false, 's' => false];
        while (true) {
            $global = $this->flagGroupAt($this->pos);
            if ($global === null || $global['terminator'] !== 0x29) {
                break;
            }
            $flags = self::withFlags($flags, $global['flags']);
            $this->pos = $global['end'];
        }
        $node = $this->alternation($flags);
        // Only an unmatched ")" stops the top-level alternation.
        if ($this->pos < count($this->cps)) {
            $this->fail('unbalanced parenthesis');
        }

        return $node;
    }

    /**
     * withFlags turns on the flags named in $set.
     *
     * @param Flags $flags
     * @param array<string, true> $set
     * @return Flags
     */
    private static function withFlags(array $flags, array $set): array
    {
        return [
            'i' => $flags['i'] || isset($set['i']),
            'm' => $flags['m'] || isset($set['m']),
            's' => $flags['s'] || isset($set['s']),
        ];
    }

    private function fail(string $reason): never
    {
        throw new InvalidArgumentException(sprintf('%s at offset %d', $reason, $this->pos));
    }

    private function peek(int $offset = 0): ?int
    {
        return $this->cps[$this->pos + $offset] ?? null;
    }

    /**
     * flagGroupAt reads "(?" [ims]+ followed by ")" or ":" at index $at,
     * without consuming it.
     *
     * @return array{flags: array<string, true>, terminator: int, end: int}|null
     */
    private function flagGroupAt(int $at): ?array
    {
        if (($this->cps[$at] ?? null) !== 0x28 || ($this->cps[$at + 1] ?? null) !== 0x3F) {
            return null;
        }
        $flags = [];
        $j = $at + 2;
        while (isset(self::FLAG_NAMES[$this->cps[$j] ?? -1])) {
            $flags[self::FLAG_NAMES[$this->cps[$j]]] = true;
            $j++;
        }
        $terminator = $this->cps[$j] ?? null;
        if ($j === $at + 2 || ($terminator !== 0x29 && $terminator !== 0x3A)) {
            return null;
        }

        return ['flags' => $flags, 'terminator' => $terminator, 'end' => $j + 1];
    }

    /**
     * @param Flags $flags
     * @return Node
     */
    private function alternation(array $flags): array
    {
        $alternatives = [$this->concat($flags)];
        while ($this->peek() === 0x7C) {
            $this->pos++;
            $alternatives[] = $this->concat($flags);
        }

        return count($alternatives) === 1 ? $alternatives[0] : ['k' => 'alt', 'items' => $alternatives];
    }

    /**
     * @param Flags $flags
     * @return Node
     */
    private function concat(array $flags): array
    {
        $items = [];
        while (true) {
            $c = $this->peek();
            if ($c === null || $c === 0x7C || $c === 0x29) {
                break;
            }
            if ($this->quantifierAt() !== null) {
                $this->fail('nothing to repeat');
            }
            $atom = $this->atom($flags);
            $q = $this->quantifierAt();
            if ($q === null) {
                $items[] = $atom;
                continue;
            }
            if ($atom['k'] === 'assert') {
                $this->fail('nothing to repeat');
            }
            $this->pos = $q['end'];
            $lazy = $this->peek() === 0x3F;
            if ($lazy) {
                $this->pos++;
            }
            if ($this->quantifierAt() !== null) {
                $this->fail('multiple repeat');
            }
            $items[] = ['k' => 'repeat', 'child' => $atom, 'min' => $q['min'], 'max' => $q['max'], 'lazy' => $lazy];
        }

        return count($items) === 1 ? $items[0] : ['k' => 'concat', 'items' => $items];
    }

    /**
     * quantifierAt reads a quantifier at the current position without
     * consuming it. A "{" that is not {n}, {n,} or {n,m} is a literal, except
     * {,...} which is refused.
     *
     * @phpstan-impure
     *
     * @return Quantifier|null
     */
    private function quantifierAt(): ?array
    {
        return match ($this->peek()) {
            0x2A => ['min' => 0, 'max' => null, 'end' => $this->pos + 1],
            0x2B => ['min' => 1, 'max' => null, 'end' => $this->pos + 1],
            0x3F => ['min' => 0, 'max' => 1, 'end' => $this->pos + 1],
            0x7B => $this->bracedQuantifier(),
            default => null,
        };
    }

    /** @return Quantifier|null */
    private function bracedQuantifier(): ?array
    {
        [$n, $j] = $this->digitsAt($this->pos + 1);
        if ($n === '') {
            if (($this->cps[$j] ?? null) === 0x2C) {
                [, $j] = $this->digitsAt($j + 1);
                if (($this->cps[$j] ?? null) === 0x7D) {
                    $this->fail('missing repeat minimum');
                }
            }

            return null;
        }
        $m = $n;
        if (($this->cps[$j] ?? null) === 0x2C) {
            [$m, $j] = $this->digitsAt($j + 1);
            $m = $m === '' ? null : $m;
        }
        if (($this->cps[$j] ?? null) !== 0x7D) {
            return null;
        }
        // Compare as digit strings first so that a huge count cannot overflow.
        $over = static fn(string $d): bool => strlen(ltrim($d, '0')) > 4 || (int) $d > self::MAX_REPEAT;
        if ($over($n) || ($m !== null && $over($m))) {
            $this->fail(sprintf('repeat count over %d', self::MAX_REPEAT));
        }
        $min = (int) $n;
        $max = $m === null ? null : (int) $m;
        if ($max !== null && $min > $max) {
            $this->fail('repeat minimum over maximum');
        }

        return ['min' => $min, 'max' => $max, 'end' => $j + 1];
    }

    /** @return array{string, int} */
    private function digitsAt(int $index): array
    {
        $digits = '';
        while (($c = $this->cps[$index] ?? null) !== null && $c >= 0x30 && $c <= 0x39) {
            $digits .= chr($c);
            $index++;
        }

        return [$digits, $index];
    }

    /**
     * @param Flags $flags
     * @return Node
     */
    private function atom(array $flags): array
    {
        $c = (int) $this->peek();
        switch ($c) {
            case 0x28:
                return $this->group($flags);
            case 0x5B:
                return $this->charClass($flags);
            case 0x2E:
                $this->pos++;

                return self::set($flags['s'] ? [[0, self::MAX_CP]] : self::complement([[0x0A, 0x0A]]));
            case 0x5E:
                $this->pos++;

                return self::assert($flags['m'] ? 'line_start' : 'start');
            case 0x24:
                $this->pos++;

                return self::assert($flags['m'] ? 'line_end' : 'end_dollar');
            case 0x5C:
                return $this->escape($flags);
            default:
                $this->pos++;

                return self::literal($c, $flags);
        }
    }

    /**
     * @param Flags $flags
     * @return Node
     */
    private function group(array $flags): array
    {
        if ($this->peek(1) !== 0x3F) {
            $this->pos++;

            return $this->groupBody($flags);
        }
        $scoped = $this->flagGroupAt($this->pos);
        if ($scoped !== null) {
            if ($scoped['terminator'] === 0x29) {
                $this->fail('global flags not at the start');
            }
            $this->pos = $scoped['end'];

            return $this->groupBody(self::withFlags($flags, $scoped['flags']));
        }
        if ($this->peek(2) === 0x3A) {
            $this->pos += 3;

            return $this->groupBody($flags);
        }
        if ($this->peek(2) === 0x50 && $this->peek(3) === 0x3C) {
            return $this->namedGroup($flags);
        }
        $this->fail('unsupported group syntax');
    }

    /**
     * @param Flags $flags
     * @return Node
     */
    private function namedGroup(array $flags): array
    {
        $this->pos += 4;
        $name = '';
        while (true) {
            $c = $this->peek();
            if ($c === null) {
                $this->fail('unterminated group name');
            }
            if ($c === 0x3E) {
                break;
            }
            // A non-ASCII code point makes the name invalid below.
            $name .= $c < 0x80 ? chr($c) : "\0";
            $this->pos++;
        }
        if (preg_match('/\A[A-Za-z_][A-Za-z0-9_]*\z/', $name) !== 1) {
            $this->fail('bad group name');
        }
        if (isset($this->names[$name])) {
            $this->fail('duplicate group name');
        }
        $this->names[$name] = true;
        $this->pos++;

        return $this->groupBody($flags);
    }

    /**
     * @param Flags $flags
     * @return Node
     */
    private function groupBody(array $flags): array
    {
        $child = $this->alternation($flags);
        if ($this->peek() !== 0x29) {
            $this->fail('missing )');
        }
        $this->pos++;

        return ['k' => 'group', 'child' => $child];
    }

    /**
     * @param Flags $flags
     * @return Node
     */
    private function escape(array $flags): array
    {
        $e = $this->peek(1);
        if ($e === null) {
            $this->fail('trailing backslash');
        }
        $shorthand = self::shorthand($e);
        if ($shorthand !== null) {
            $this->pos += 2;

            return self::set($shorthand);
        }
        $kind = $e < 0x80 ? (self::ASSERT_ESCAPES[chr($e)] ?? null) : null;
        if ($kind !== null) {
            $this->pos += 2;

            return self::assert($kind);
        }

        return self::literal($this->singleEscape(), $flags);
    }

    /**
     * singleEscape reads an escape that stands for one code point:
     * \t \n \r \f \v \a, \xHH, or a backslash before ASCII punctuation.
     */
    private function singleEscape(): int
    {
        $e = $this->peek(1);
        if ($e === null) {
            $this->fail('trailing backslash');
        }
        $control = $e < 0x80 ? (self::CONTROL_ESCAPES[chr($e)] ?? null) : null;
        if ($control !== null) {
            $this->pos += 2;

            return $control;
        }
        if ($e === 0x78) {
            $h = self::hexValue($this->peek(2));
            $l = self::hexValue($this->peek(3));
            if ($h === null || $l === null) {
                $this->fail('\x needs two hex digits');
            }
            $this->pos += 4;

            return $h * 16 + $l;
        }
        if ($e < 0x80 && !self::asciiAlnum($e)) {
            $this->pos += 2;

            return $e;
        }
        $this->fail($e < 0x80 ? sprintf('unsupported escape \%s', chr($e)) : 'unsupported escape of a non-ASCII character');
    }

    /**
     * @param Flags $flags
     * @return Node
     */
    private function charClass(array $flags): array
    {
        $this->pos++;
        $negated = $this->peek() === 0x5E;
        if ($negated) {
            $this->pos++;
        }
        $members = [];
        $first = true;
        while (true) {
            $c = $this->peek();
            if ($c === null) {
                $this->fail('unterminated character class');
            }
            if ($c === 0x5D && !$first) {
                break;
            }
            $first = false;
            $this->classMember($members);
        }
        $this->pos++;
        $ranges = self::normalize($members);
        if ($flags['i']) {
            $ranges = self::foldAscii($ranges);
        }
        if ($negated) {
            $ranges = self::complement($ranges);
        }

        return self::set($ranges);
    }

    /**
     * classMember reads one member (a code point, a range or a shorthand) and
     * appends its ranges to $members.
     *
     * @param list<Range> $members
     */
    private function classMember(array &$members): void
    {
        [$shorthand, $value] = $this->classItem();
        $range = $this->peek() === 0x2D && $this->peek(1) !== null && $this->peek(1) !== 0x5D;
        if ($shorthand !== null) {
            if ($range) {
                $this->fail('bad character range');
            }
            array_push($members, ...$shorthand);

            return;
        }
        if (!$range) {
            $members[] = [$value, $value];

            return;
        }
        $this->pos++;
        [$endShorthand, $endValue] = $this->classItem();
        if ($endShorthand !== null || $value > $endValue) {
            $this->fail('bad character range');
        }
        $members[] = [$value, $endValue];
    }

    /**
     * classItem reads a shorthand (its ranges, value 0) or a single code
     * point (null, the code point).
     *
     * @return array{list<Range>|null, int}
     */
    private function classItem(): array
    {
        $c = (int) $this->peek();
        if ($c === 0x5B && $this->peek(1) === 0x3A) {
            $this->fail('POSIX class');
        }
        if ($c !== 0x5C) {
            $this->pos++;

            return [null, $c];
        }
        $shorthand = self::shorthand($this->peek(1));
        if ($shorthand !== null) {
            $this->pos += 2;

            return [$shorthand, 0];
        }

        return [null, $this->singleEscape()];
    }

    private static function asciiAlnum(int $code): bool
    {
        return ($code >= 0x30 && $code <= 0x39) || ($code >= 0x41 && $code <= 0x5A) || ($code >= 0x61 && $code <= 0x7A);
    }

    private static function hexValue(?int $code): ?int
    {
        return match (true) {
            $code === null => null,
            $code >= 0x30 && $code <= 0x39 => $code - 0x30,
            $code >= 0x41 && $code <= 0x46 => $code - 0x41 + 10,
            $code >= 0x61 && $code <= 0x66 => $code - 0x61 + 10,
            default => null,
        };
    }

    // ---- Stage 2: AST ----

    /**
     * literal is the set for code point $code: under i an ASCII letter also
     * matches its other case; non-ASCII letters never fold.
     *
     * @param Flags $flags
     * @return Node
     */
    private static function literal(int $code, array $flags): array
    {
        $ranges = [[$code, $code]];

        return self::set($flags['i'] ? self::foldAscii($ranges) : $ranges);
    }

    /**
     * @param list<Range> $ranges
     * @return Node
     */
    private static function set(array $ranges): array
    {
        return ['k' => 'set', 'r' => $ranges];
    }

    /** @return Node */
    private static function assert(string $kind): array
    {
        return ['k' => 'assert', 'kind' => $kind];
    }

    // ---- Stage 3: emit (PHP column) ----

    private static function cp(int $code): string
    {
        return '\x{' . strtoupper(dechex($code)) . '}';
    }

    /** @param list<Range> $ranges */
    private static function emitSet(array $ranges): string
    {
        $ranges = self::withoutSurrogates($ranges);
        if ($ranges === []) {
            return '(?!)';
        }
        if (count($ranges) === 1 && $ranges[0][0] === $ranges[0][1]) {
            return self::cp($ranges[0][0]);
        }
        $body = '';
        foreach ($ranges as [$lo, $hi]) {
            $body .= $lo === $hi ? self::cp($lo) : self::cp($lo) . '-' . self::cp($hi);
        }

        return '[' . $body . ']';
    }

    /** @param Node $node */
    private static function emit(array $node): string
    {
        return match ($node['k']) {
            'set' => self::emitSet($node['r']),
            'concat' => implode('', array_map(self::emit(...), $node['items'])),
            'alt' => implode('|', array_map(self::emit(...), $node['items'])),
            'group' => '(?:' . self::emit($node['child']) . ')',
            'assert' => self::emitAssert($node['kind']),
            default => self::emitRepeat($node),
        };
    }

    /** @param Node $node */
    private static function emitRepeat(array $node): string
    {
        $operand = self::emit($node['child']);
        // A bare lookahead is not a repeat target.
        if ($operand === '(?!)') {
            $operand = '(?:(?!))';
        }
        $min = $node['min'];
        $max = $node['max'];
        $q = match (true) {
            $max === null && $min === 0 => '*',
            $max === null && $min === 1 => '+',
            $max === null => '{' . $min . ',}',
            $min === 0 && $max === 1 => '?',
            $min === $max => '{' . $min . '}',
            default => '{' . $min . ',' . $max . '}',
        };

        return $operand . $q . ($node['lazy'] ? '?' : '');
    }

    private static function emitAssert(string $kind): string
    {
        $lf = self::cp(0x0A);
        $w = self::emitSet(self::WORD);

        return match ($kind) {
            'start' => '\A',
            'end_dollar' => "(?={$lf}?\\z)",
            'line_start' => "(?:\\A|(?<={$lf}))",
            'line_end' => "(?={$lf}|\\z)",
            'word_b' => "(?:(?<={$w})(?!{$w})|(?<!{$w})(?={$w}))",
            default => "(?:(?<={$w})(?={$w})|(?<!{$w})(?!{$w}))",
        };
    }
}
