<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Events;

use MegooFlags\Internal\Events\Recorder;
use PHPUnit\Framework\TestCase;

final class RecorderTest extends TestCase
{
    private static function at(int $hour, int $minute, int $second, float $fraction = 0.0): float
    {
        return gmmktime($hour, $minute, $second, 11, 10, 2026) + $fraction;
    }

    /**
     * @return array{counts: list<list<mixed>>, exposures: list<array<string, mixed>>}
     */
    private static function line(Recorder $recorder): array
    {
        $line = $recorder->take();
        self::assertIsString($line);
        self::assertStringNotContainsString("\n", $line);
        /** @var array{counts: list<list<mixed>>, exposures: list<array<string, mixed>>} $data */
        $data = json_decode($line, true, 512, JSON_THROW_ON_ERROR);

        return $data;
    }

    public function testCountsPerFlagVariantAndUtcHour(): void
    {
        $recorder = new Recorder(false);
        $recorder->record('a', 'on', true, 'TARGETING_MATCH', 'u1', [], self::at(9, 1, 0));
        $recorder->record('a', 'on', true, 'TARGETING_MATCH', 'u2', [], self::at(9, 59, 59));
        $recorder->record('a', 'off', true, 'TARGETING_MATCH', 'u1', [], self::at(9, 30, 0));
        $recorder->record('a', 'on', true, 'TARGETING_MATCH', 'u1', [], self::at(10, 0, 0));
        $recorder->record('b', 'on', true, 'STATIC', 'u1', [], self::at(9, 5, 0));

        $nine = gmmktime(9, 0, 0, 11, 10, 2026);
        $ten = gmmktime(10, 0, 0, 11, 10, 2026);
        self::assertSame([
            ['a', 'on', $nine, 2],
            ['a', 'off', $nine, 1],
            ['a', 'on', $ten, 1],
            ['b', 'on', $nine, 1],
        ], self::line($recorder)['counts']);
    }

    public function testUnservedEvaluationsCountWithNullVariantAndNoExposure(): void
    {
        $recorder = new Recorder(false);
        $recorder->record('a', null, false, 'ERROR', 'u1', [], self::at(9, 0, 0));
        $recorder->record('a', null, false, 'ERROR', 'u2', [], self::at(9, 1, 0));
        // An unserved evaluation keeps its own bucket even when a variant is given.
        $recorder->record('a', 'null', false, 'ERROR', 'u3', [], self::at(9, 2, 0));

        $line = self::line($recorder);
        self::assertSame([['a', null, gmmktime(9, 0, 0, 11, 10, 2026), 3]], $line['counts']);
        self::assertSame([], $line['exposures']);
    }

    public function testOneExposurePerContextKeyFlagVariantAndHour(): void
    {
        $recorder = new Recorder(false);
        $recorder->record('a', 'on', true, 'TARGETING_MATCH', 'u1', [], self::at(9, 15, 2));
        $recorder->record('a', 'on', true, 'TARGETING_MATCH', 'u1', [], self::at(9, 20, 0)); // same hour
        $recorder->record('a', 'off', true, 'TARGETING_MATCH', 'u1', [], self::at(9, 21, 0)); // other variant
        $recorder->record('a', 'on', true, 'TARGETING_MATCH', 'u2', [], self::at(9, 22, 0)); // other key
        $recorder->record('b', 'on', true, 'TARGETING_MATCH', 'u1', [], self::at(9, 23, 0)); // other flag
        $recorder->record('a', 'on', true, 'TARGETING_MATCH', 'u1', [], self::at(10, 0, 1)); // other hour
        $recorder->record('a', 'on', true, 'TARGETING_MATCH', '', [], self::at(9, 24, 0)); // anonymous
        $recorder->record('a', 'on', true, 'TARGETING_MATCH', '', [], self::at(9, 25, 0)); // anonymous again

        $exposures = self::line($recorder)['exposures'];
        $keys = array_map(
            static fn(array $e): string => $e['context_key'] . '|' . $e['flag_key'] . '|' . $e['variant'] . '|' . $e['timestamp'],
            $exposures,
        );
        self::assertSame([
            'u1|a|on|2026-11-10T09:15:02.000Z',
            'u1|a|off|2026-11-10T09:21:00.000Z',
            'u2|a|on|2026-11-10T09:22:00.000Z',
            'u1|b|on|2026-11-10T09:23:00.000Z',
            'u1|a|on|2026-11-10T10:00:01.000Z',
            '|a|on|2026-11-10T09:24:00.000Z',
        ], $keys);
    }

    public function testContextOnlyWithSendContext(): void
    {
        $without = new Recorder(false);
        $without->record('a', 'on', true, 'STATIC', 'u1', ['plan' => 'pro'], self::at(9, 0, 0));
        self::assertArrayNotHasKey('context', self::line($without)['exposures'][0]);

        $with = new Recorder(true);
        $with->record('a', 'on', true, 'STATIC', 'u1', ['plan' => 'pro', 'key' => 'u1'], self::at(9, 0, 0));
        $with->record('b', 'on', true, 'STATIC', 'u1', [], self::at(9, 0, 0));
        $text = (string) $with->take();
        $data = json_decode($text, false, 512, JSON_THROW_ON_ERROR);
        self::assertEquals((object) ['plan' => 'pro', 'key' => 'u1'], $data->exposures[0]->context);
        // An empty context stays a JSON object.
        self::assertEquals(new \stdClass(), $data->exposures[1]->context);
    }

    public function testSpoolLineShape(): void
    {
        $recorder = new Recorder(false);
        $recorder->record('a', 'on', true, 'RULE_MATCH', 'u1', [], self::at(9, 15, 2, 0.25));
        $recorder->record('a', null, false, 'ERROR', 'u1', [], self::at(9, 15, 3));

        self::assertSame(
            '{"counts":[["a","on",' . gmmktime(9, 0, 0, 11, 10, 2026) . ',1],["a",null,' . gmmktime(9, 0, 0, 11, 10, 2026) . ',1]],'
            . '"exposures":[{"flag_key":"a","variant":"on","reason":"RULE_MATCH","timestamp":"2026-11-10T09:15:02.250Z","context_key":"u1"}]}',
            $recorder->take(),
        );
    }

    public function testTakeClearsAndIsNullWhenEmpty(): void
    {
        $recorder = new Recorder(false);
        self::assertNull($recorder->take());
        $recorder->record('a', 'on', true, 'STATIC', 'u1', [], self::at(9, 0, 0));
        self::assertNotNull($recorder->take());
        self::assertNull($recorder->take());

        // After take, the same exposure is recorded again (a new spool line).
        $recorder->record('a', 'on', true, 'STATIC', 'u1', [], self::at(9, 0, 0));
        self::assertCount(1, self::line($recorder)['exposures']);
    }

    public function testPendingExposuresAreCappedButCountsAreNot(): void
    {
        $recorder = new Recorder(false, 2);
        foreach (['u1', 'u2', 'u3', 'u4'] as $key) {
            $recorder->record('a', 'on', true, 'STATIC', $key, [], self::at(9, 0, 0));
        }

        $line = self::line($recorder);
        self::assertCount(2, $line['exposures']);
        self::assertSame(4, $line['counts'][0][3]);
        self::assertSame(2, $recorder->dropped());
    }
}
