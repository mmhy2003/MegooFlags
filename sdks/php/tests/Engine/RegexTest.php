<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Engine;

use MegooFlags\Internal\Engine\Regex;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class RegexTest extends TestCase
{
    private const LF = "\n";
    private const CR = "\r";
    private const VT = "\v";

    private const W = '[\x{30}-\x{39}\x{41}-\x{5A}\x{5F}\x{61}-\x{7A}]';

    /**
     * Python re with re.ASCII semantics.
     *
     * @return list<array{string, string, bool}>
     */
    private static function vectorTable(): array
    {
        $lf = self::LF;
        $cr = self::CR;
        $vt = self::VT;

        return [
            ['b', 'abc', true],
            ['b', 'xyz', false],
            ['^ab$', 'ab', true],
            ['^ab$', 'abc', false],
            ['^ab$', 'xab', false],
            ['^ab$', "ab{$lf}", true],
            ['^ab$', "ab{$lf}{$lf}", false],
            ['^ab$', "ab{$cr}", false],
            ['\d', 'a3', true],
            ['\d', "\u{0663}", false],
            ['(?i)k', 'K', true],
            ['(?i)k', "\u{212A}", false],
            ['(?i)^[s]$', 'S', true],
            ['(?i)^[s]$', "\u{017F}", false],
            ['(?i)^[j-l]$', 'K', true],
            ['(?i)^[j-l]$', "\u{212A}", false],
            ['(?i)é', 'é', true],
            ['(?i)é', 'É', false],
            ['(?i)[^a]', 'A', false],
            ['(?i)[^a]', 'b', true],
            ['^(?i:ab)c$', 'ABc', true],
            ['^(?i:ab)c$', 'ABC', false],
            ['(?im)^B$', "a{$lf}b", true],
            ['^\s$', $vt, true],
            ['^\s$', ' ', true],
            ['^\s$', "\u{00A0}", false],
            ['^\s$', "\u{0085}", false],
            ['^\s$', "\u{2028}", false],
            ['^\S$', $vt, false],
            ['^\S$', "\u{00A0}", true],
            ['^[\s]$', $vt, true],
            ['^[^\d]$', 'x', true],
            ['^[^\d]$', '5', false],
            ['^[\w-]$', '-', true],
            ['^[\w-]$', 'a', true],
            ['^[\w-]$', 'é', false],
            ['^\w+$', 'a_1', true],
            ['^\w+$', 'é', false],
            ['a\bé', 'aé', true],
            ['a\Bé', 'aé', false],
            ['\bfoo\b', 'a foo b', true],
            ['\bfoo\b', 'afoo', false],
            ['^.$', $cr, true],
            ['^.$', $lf, false],
            ['^.$', "\u{2028}", true],
            ['^.$', "\u{1F600}", true],
            ['^..$', "\u{1F600}", false],
            ["^[\u{1F600}a]$", "\u{1F600}", true],
            ["^[\u{1F600}a]$", 'a', true],
            ['^a.b$', "a{$lf}b", false],
            ['(?s)^a.b$', "a{$lf}b", true],
            ['^b$', "a{$lf}b{$lf}c", false],
            ['(?m)^b$', "a{$lf}b{$lf}c", true],
            ['(?m)a$', "a{$lf}b", true],
            ['(?m)a$', "a{$cr}{$lf}b", false],
            ['(?m)^$', "a{$lf}{$lf}b", true],
            ['(?m)\Ab', "b{$lf}a", true],
            ['(?m)\Ab', "a{$lf}b", false],
            ['^$', '', true],
            ['^$', $lf, true],
            ['', '', true],
            ['', 'x', true],
            ['^(?P<x>ab)+c$', 'ababc', true],
            ['^(?:a|b|)$', '', true],
            ['^(?:a|b|)$', 'c', false],
            ['^a{2,3}$', 'aa', true],
            ['^a{2,3}$', 'aaaa', false],
            ['^a{2,}$', 'aaaaa', true],
            ['^a*?$', 'aaa', true],
            ['x{', 'x{', true],
            ['x{a}', 'x{a}', true],
            ['^\x41\t\.$', "A\t.", true],
            ['^[a[]$', '[', true],
            ['^[a[]$', 'a', true],
            ['^[a[]$', 'b', false],
            ['^[a&&b]$', '&', true],
            ['^[a\-z]$', '-', true],
            ['^[a\-z]$', 'b', false],
            ['^[a-]$', '-', true],
            ['^[]a]$', ']', true],
            ['^[^]a]$', 'b', true],
            ['^[^]a]$', ']', false],
            ['^[a-c-e]$', '-', true],
            ['^[a-c-e]$', 'd', false],
            ['^a\|b$', 'a|b', true],
        ];
    }

    /** @return iterable<string, array{string, string, bool}> */
    public static function vectors(): iterable
    {
        foreach (self::vectorTable() as $i => $row) {
            yield sprintf('#%d %s against %s', $i, json_encode($row[0]), json_encode($row[1])) => $row;
        }
    }

    #[DataProvider('vectors')]
    public function testVector(string $pattern, string $subject, bool $expected): void
    {
        self::assertSame($expected, Regex::test(self::translated($pattern), $subject));
    }

    /** @return iterable<string, array{string}> */
    public static function refused(): iterable
    {
        $patterns = [
            '(?=a)', '(?!a)', '(?<=a)b', '(?<!a)b', '(a)\1', '(?P<n>a)(?P=n)', '(?P<n>a)(?P<n>b)', '(?>a)',
            '(?(1)a|b)', '(?<n>a)', '(?#c)a', 'a*+', 'a++', 'a?+', 'a**', 'a{2}{3}', '*a', '(*a)', 'a|*', '^*',
            '\b+', 'a{,2}', 'a{3,2}', 'a{1001}', '\z', '\Z', '\pL', '\x{41}', '\N{LATIN CAPITAL LETTER A}', '\e',
            '\q', '\0', '\\', '(?x)a', '(?U)a', '(?a)a', 'a(?i)b', '(?-i:a)', '[[:alpha:]]', '[a-\d]', '[\w-a]',
            '[z-a]', '[\b]', '[]', '[a', '(', ')', '(a',
            // Not valid UTF-8.
            "a\xFF",
        ];
        foreach ($patterns as $pattern) {
            yield json_encode($pattern, JSON_INVALID_UTF8_SUBSTITUTE) ?: $pattern => [$pattern];
        }
    }

    #[DataProvider('refused')]
    public function testRefused(string $pattern): void
    {
        $error = null;

        self::assertNull(Regex::translate($pattern, $error));
        self::assertIsString($error);
        self::assertNotSame('', $error);
    }

    /** @return iterable<string, array{string, string}> */
    public static function emitted(): iterable
    {
        $w = self::W;

        yield '^a$' => ['^a$', '~(*UTF)\A\x{61}(?=\x{A}?\z)~'];
        yield '(?m)^a$' => ['(?m)^a$', '~(*UTF)(?:\A|(?<=\x{A}))\x{61}(?=\x{A}|\z)~'];
        yield '\bx' => ['\bx', "~(*UTF)(?:(?<={$w})(?!{$w})|(?<!{$w})(?={$w}))\\x{78}~"];
        yield '(?i)[a-c]' => ['(?i)[a-c]', '~(*UTF)[\x{41}-\x{43}\x{61}-\x{63}]~'];
    }

    #[DataProvider('emitted')]
    public function testEmitter(string $pattern, string $pcre): void
    {
        self::assertSame($pcre, self::translated($pattern));
    }

    public function testNeverUsesHostFlagsOrLineAnchors(): void
    {
        foreach (self::vectorTable() as [$pattern]) {
            $pcre = self::translated($pattern);
            $source = substr($pcre, strlen('~(*UTF)'), -1);

            self::assertStringStartsWith('~(*UTF)', $pcre, $pattern);
            self::assertStringEndsWith('~', $pcre, $pattern);
            self::assertStringNotContainsString('~', $source, $pattern);
            self::assertStringNotContainsString('^', $source, $pattern);
            self::assertStringNotContainsString('$', $source, $pattern);
        }
    }

    public function testSetsLeaveOutSurrogates(): void
    {
        self::assertSame('~(*UTF)[\x{0}-\x{9}\x{B}-\x{D7FF}\x{E000}-\x{10FFFF}]~', self::translated('.'));
        self::assertTrue(Regex::test(self::translated('^.$'), "\u{E000}"));
    }

    public function testRepeatsAnEmptySet(): void
    {
        $pcre = self::translated('^[^\s\S]*$');

        self::assertTrue(Regex::test($pcre, ''));
        self::assertFalse(Regex::test($pcre, 'a'));
    }

    public function testInvalidUtf8SubjectNeverMatches(): void
    {
        self::assertFalse(Regex::test(self::translated('a'), "a\xFF"));
        self::assertFalse(Regex::test(self::translated(''), "\xC3"));
        self::assertFalse(Regex::test(self::translated(''), "\xED\xA0\x80"));
    }

    public function testErrorIsClearedOnSuccess(): void
    {
        $error = 'stale';

        self::assertNotNull(Regex::translate('a', $error));
        self::assertNull($error);
    }

    private static function translated(string $pattern): string
    {
        $error = null;
        $pcre = Regex::translate($pattern, $error);
        self::assertNotNull($pcre, sprintf('%s: %s', $pattern, $error ?? ''));

        return $pcre;
    }
}
