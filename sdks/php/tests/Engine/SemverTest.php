<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Engine;

use MegooFlags\Internal\Engine\Semver;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class SemverTest extends TestCase
{
    /** @return iterable<string, array{string}> */
    public static function valid(): iterable
    {
        foreach (['1.2.3', '1.0.0-alpha.1', '1.0.0+build.5', '1.0.0-0.3.7', '1.0.0-x-y-z.--', '1.0.0-alpha+b.1', '0.0.0'] as $s) {
            yield $s => [$s];
        }
    }

    #[DataProvider('valid')]
    public function testValid(string $text): void
    {
        self::assertNotNull(Semver::parse(json_decode(json_encode($text, JSON_THROW_ON_ERROR), false)));
    }

    /** @return iterable<string, array{string}> */
    public static function invalid(): iterable
    {
        yield 'v prefix' => ['"v1.2.3"'];
        yield 'two parts' => ['"1.2"'];
        yield 'four parts' => ['"1.2.3.4"'];
        yield 'leading zero' => ['"01.2.3"'];
        yield 'numeric prerelease leading zero' => ['"1.2.3-01"'];
        yield 'empty prerelease' => ['"1.2.3-"'];
        yield 'empty build' => ['"1.2.3+"'];
        yield 'empty identifier' => ['"1.2.3-a..b"'];
        yield 'trailing newline' => ['"1.2.3\n"'];
        yield 'leading space' => ['" 1.2.3"'];
        yield 'arabic-indic digit' => ['"1.2.٣"'];
        yield 'non-ascii prerelease' => ['"1.2.3-α"'];
        yield 'empty' => ['""'];
        yield 'empty core part' => ['"1..3"'];
    }

    #[DataProvider('invalid')]
    public function testInvalid(string $json): void
    {
        $text = json_decode($json, false);
        self::assertIsString($text);
        self::assertNull(Semver::parse($text));
    }

    public function testOrder(): void
    {
        $order = [
            '1.0.0-alpha', '1.0.0-alpha.1', '1.0.0-alpha.beta', '1.0.0-beta',
            '1.0.0-beta.2', '1.0.0-beta.11', '1.0.0-rc.1', '1.0.0',
        ];
        for ($i = 0; $i < count($order) - 1; $i++) {
            $lower = self::parse($order[$i]);
            $higher = self::parse($order[$i + 1]);
            self::assertSame(-1, Semver::compare($lower, $higher), $order[$i] . ' < ' . $order[$i + 1]);
            self::assertSame(1, Semver::compare($higher, $lower), $order[$i + 1] . ' > ' . $order[$i]);
        }
    }

    public function testBuildIsIgnored(): void
    {
        self::assertSame(0, Semver::compare(self::parse('1.0.0+b1'), self::parse('1.0.0+b2')));
    }

    public function testLargeCoreParts(): void
    {
        self::assertSame(1, Semver::compare(self::parse('99999999999999999999.0.0'), self::parse('9.0.0')));
        self::assertSame(-1, Semver::compare(self::parse('1.2.3'), self::parse('1.10.0')));
    }

    /** @return array{list<string>, list<array{bool, string}>|null} */
    private static function parse(string $text): array
    {
        $version = Semver::parse($text);
        self::assertNotNull($version, $text);

        return $version;
    }
}
