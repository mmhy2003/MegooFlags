<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Cache;

use MegooFlags\Internal\Cache\State;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class StateTest extends TestCase
{
    private string $dir;

    private string $path;

    protected function setUp(): void
    {
        $this->dir = sys_get_temp_dir() . '/megooflags-state-' . bin2hex(random_bytes(6));
        $this->path = $this->dir . '/megooflags-0123456789abcdef.state.json';
    }

    protected function tearDown(): void
    {
        foreach (glob($this->dir . '/{,.}*', GLOB_BRACE) ?: [] as $file) {
            if (is_file($file)) {
                unlink($file);
            }
        }
        if (is_dir($this->dir)) {
            rmdir($this->dir);
        }
    }

    private static function assertDefaults(State $state): void
    {
        self::assertSame(0.0, $state->checkedAt);
        self::assertSame('', $state->etag);
        self::assertSame(0, $state->failures);
        self::assertSame(0.0, $state->nextAttempt);
        self::assertNull($state->fatal);
    }

    public function testMissingFileGivesDefaults(): void
    {
        self::assertDefaults(State::load($this->path));
    }

    /**
     * @return iterable<string, array{string}>
     */
    public static function corrupt(): iterable
    {
        yield 'not json' => ['nope'];
        yield 'truncated' => ['{"checked_at": 1.5, "etag"'];
        yield 'list' => ['[1, 2]'];
        yield 'wrong types' => ['{"checked_at": "x", "etag": 1, "failures": "2", "next_attempt": null, "fatal": 401}'];
        yield 'unknown fatal' => ['{"checked_at": 1.0, "etag": "e", "failures": 0, "next_attempt": 0.0, "fatal": "500"}'];
    }

    #[DataProvider('corrupt')]
    public function testCorruptFileGivesDefaults(string $content): void
    {
        mkdir($this->dir);
        file_put_contents($this->path, $content);

        self::assertDefaults(State::load($this->path));
    }

    public function testRoundTripIsAtomicAndPrivate(): void
    {
        $state = new State(checkedAt: 1763200000.25, etag: '"v7"', failures: 3, nextAttempt: 1763200012.5);

        $state->save($this->path);

        $file = json_decode((string) file_get_contents($this->path), true, 512, JSON_THROW_ON_ERROR);
        self::assertSame(
            ['checked_at' => 1763200000.25, 'etag' => '"v7"', 'failures' => 3, 'next_attempt' => 1763200012.5, 'fatal' => null],
            $file,
        );
        self::assertEquals($state, State::load($this->path));
        self::assertSame(0o600, fileperms($this->path) & 0o777);
        self::assertSame([basename($this->path)], array_values(array_diff(scandir($this->dir) ?: [], ['.', '..'])));
    }

    public function testWholeSecondsLoadAsFloats(): void
    {
        mkdir($this->dir);
        file_put_contents($this->path, '{"checked_at": 10, "etag": "", "failures": 0, "next_attempt": 20, "fatal": null}');

        $state = State::load($this->path);

        self::assertSame(10.0, $state->checkedAt);
        self::assertSame(20.0, $state->nextAttempt);
    }

    /**
     * @return iterable<string, array{string}>
     */
    public static function fatalMarkers(): iterable
    {
        yield '401' => ['401'];
        yield '403' => ['403'];
        yield 'revoked' => ['revoked'];
    }

    #[DataProvider('fatalMarkers')]
    public function testFatalMarkerRoundTrips(string $marker): void
    {
        $state = new State(etag: 'e');
        $state->fatal = $marker;

        $state->save($this->path);

        $loaded = State::load($this->path);
        self::assertSame($marker, $loaded->fatal);
        self::assertTrue($loaded->isFatal());
        self::assertFalse(State::load($this->dir . '/missing.json')->isFatal());
    }
}
