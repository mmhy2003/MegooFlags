<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Daemon;

use MegooFlags\Internal\Daemon\SseParser;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use RuntimeException;

final class SseParserTest extends TestCase
{
    /**
     * read feeds $chunks to a new parser and returns what it reported.
     *
     * @return array{lines: int, retries: list<int>, events: list<array{string, string, string}>}
     */
    private static function read(string ...$chunks): array
    {
        $seen = ['lines' => 0, 'retries' => [], 'events' => []];
        $parser = new SseParser(
            static function () use (&$seen): void {
                $seen['lines']++;
            },
            static function (int $ms) use (&$seen): void {
                $seen['retries'][] = $ms;
            },
            static function (string $name, string $data, string $id) use (&$seen): void {
                $seen['events'][] = [$name, $data, $id];
            },
        );
        foreach ($chunks as $chunk) {
            $parser->feed($chunk);
        }

        return $seen;
    }

    /**
     * splits cuts $text in one piece, byte by byte, and in a few chunk sizes.
     *
     * @return array<string, list<string>>
     */
    private static function splits(string $text): array
    {
        $splits = ['whole' => [$text], 'bytes' => str_split($text, 1) ?: ['']];
        foreach ([2, 3, 5, 7] as $size) {
            $splits['size ' . $size] = str_split($text, $size) ?: [''];
        }

        return $splits;
    }

    /** @return array<string, array{string, int, list<array{string, string, string}>}> */
    public static function cases(): array
    {
        return [
            'named event with id' => ["event: put\nid: 42\ndata: {\"flags\":{}}\n\n", 4, [['put', '{"flags":{}}', '42']]],
            'default name' => ["data: x\n\n", 2, [['message', 'x', '']]],
            'multi-line data' => ["data: a\ndata: b\ndata:c\n\n", 4, [['message', "a\nb\nc", '']]],
            'CRLF endings' => ["event: put\r\nid: 7\r\ndata: x\r\n\r\n", 4, [['put', 'x', '7']]],
            'unknown event name passed through' => ["event: mystery\ndata: 1\n\n", 3, [['mystery', '1', '']]],
            'unterminated last event discarded' => ["data: a\n\nevent: put\ndata: b\n", 4, [['message', 'a', '']]],
            'unterminated last line discarded' => ["data: a\n\ndata: b", 2, [['message', 'a', '']]],
            'state resets after dispatch' => ["event: put\nid: 1\ndata: a\n\ndata: b\n\n", 6, [['put', 'a', '1'], ['message', 'b', '']]],
            'empty line without data dispatches nothing' => ["event: put\n\ndata: a\n\n", 4, [['message', 'a', '']]],
            'id with NUL ignored' => ["id: a\0b\ndata: x\n\n", 3, [['message', 'x', '']]],
            'field without colon has an empty value' => ["data\ndata\n\n", 3, [['message', "\n", '']]],
            'only one leading space removed, unknown field ignored' => ["foo: bar\ndata:  two\n\n", 3, [['message', ' two', '']]],
            'colon inside the value kept' => ["data: a: b\n\n", 2, [['message', 'a: b', '']]],
            'comments call onLine only' => [": ping\n:\n\n", 3, []],
            'a CR split from its LF is one line ending' => ["data: a\r\n\r\n", 2, [['message', 'a', '']]],
            'multi-byte characters' => ["data: é€😀\n\n", 2, [['message', 'é€😀', '']]],
        ];
    }

    /** @param list<array{string, string, string}> $events */
    #[DataProvider('cases')]
    public function testCasesAtEveryChunkBoundary(string $text, int $lines, array $events): void
    {
        foreach (self::splits($text) as $label => $chunks) {
            $seen = self::read(...$chunks);

            self::assertSame($events, $seen['events'], $label);
            self::assertSame($lines, $seen['lines'], $label);
            self::assertSame([], $seen['retries'], $label);
        }
    }

    public function testAValidRetryCallsOnRetryInMilliseconds(): void
    {
        foreach (self::splits("retry: 3000\n\n") as $label => $chunks) {
            self::assertSame([3000], self::read(...$chunks)['retries'], $label);
        }
    }

    public function testAnInvalidRetryIsIgnored(): void
    {
        foreach (["retry: x\n", "retry: -1\n", "retry: 1.5\n", "retry:\n", "retry: ３\n", "retry: 1 \n"] as $text) {
            self::assertSame([], self::read($text)['retries'], $text);
        }
    }

    public function testReadsA1MiBDataLine(): void
    {
        $big = str_repeat('x', 1 << 20);
        $seen = self::read("event: put\ndata: ", ...[...str_split($big, 16384), "\n\n"]);

        self::assertCount(1, $seen['events']);
        self::assertSame($big, $seen['events'][0][1]);
    }

    public function testAMultiByteCharacterSplitAcrossTwoChunksStaysWhole(): void
    {
        $text = "data: é€😀\n\n";
        $at = (int) strpos($text, "\xE2") + 1;

        $seen = self::read(substr($text, 0, $at), substr($text, $at));

        self::assertSame([['message', 'é€😀', '']], $seen['events']);
    }

    public function testAnExceptionFromOnEventStopsReading(): void
    {
        $names = [];
        $parser = new SseParser(
            static function (): void {},
            static function (int $ms): void {},
            static function (string $name) use (&$names): void {
                $names[] = $name;

                throw new RuntimeException('stop');
            },
        );

        try {
            $parser->feed("event: revoked\ndata: {}\n\nevent: put\ndata: x\n\n");
            self::fail('the exception was swallowed');
        } catch (RuntimeException $e) {
            self::assertSame('stop', $e->getMessage());
        }
        self::assertSame(['revoked'], $names);
    }
}
