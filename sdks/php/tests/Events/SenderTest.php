<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Events;

use MegooFlags\Internal\Events\Sender;
use MegooFlags\Internal\Transport\Http;
use MegooFlags\Tests\Support\FakeServer;
use PHPUnit\Framework\TestCase;

final class SenderTest extends TestCase
{
    private const KEY = 'mf_server_sender-secret';

    private FakeServer $server;

    protected function setUp(): void
    {
        $this->server = FakeServer::start();
    }

    protected function tearDown(): void
    {
        $this->server->stop();
    }

    private function sender(?\Closure $dedup = null): Sender
    {
        return new Sender(new Http($this->server->url(), self::KEY), $dedup);
    }

    private static function hour(int $hour): int
    {
        return gmmktime($hour, 0, 0, 11, 10, 2026);
    }

    /**
     * @param list<list<mixed>> $counts
     * @param list<array<string, mixed>> $exposures
     */
    private static function line(array $counts, array $exposures = []): string
    {
        return json_encode(['counts' => $counts, 'exposures' => $exposures], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    }

    /**
     * @param array<string, mixed> $context
     * @return array<string, mixed>
     */
    private static function exposure(string $contextKey, string $flag = 'a', string $variant = 'on', string $timestamp = '2026-11-10T09:15:02.000Z', ?array $context = null): array
    {
        $e = ['flag_key' => $flag, 'variant' => $variant, 'reason' => 'TARGETING_MATCH', 'timestamp' => $timestamp, 'context_key' => $contextKey];
        if ($context !== null) {
            $e['context'] = (object) $context;
        }

        return $e;
    }

    /**
     * @return list<\stdClass>
     */
    private function bodies(): array
    {
        $bodies = [];
        foreach ($this->server->requests() as $request) {
            self::assertSame('POST', $request['method']);
            self::assertSame('/sdk/v1/events', $request['path']);
            $body = json_decode($request['body'], false, 512, JSON_THROW_ON_ERROR);
            self::assertInstanceOf(\stdClass::class, $body);
            $bodies[] = $body;
        }

        return $bodies;
    }

    public function testBodyShapeAndHeaders(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202]);
        $line = self::line(
            [['a', 'on', self::hour(9), 2], ['a', null, self::hour(9), 1]],
            [self::exposure('u1', context: ['plan' => 'pro']), self::exposure('u2')],
        );

        $result = $this->sender()->ship([$line]);

        self::assertSame(['dropped' => 0, 'retryAfter' => null, 'remainder' => null, 'fatal' => null], $result);
        $requests = $this->server->requests();
        self::assertCount(1, $requests);
        self::assertSame('Bearer ' . self::KEY, $requests[0]['headers']['authorization']);
        self::assertSame('megooflags-php/0.1.0', $requests[0]['headers']['user-agent']);
        self::assertSame('application/json', $requests[0]['headers']['content-type']);
        self::assertSame(
            '{"exposures":['
            . '{"flag_key":"a","variant":"on","reason":"TARGETING_MATCH","timestamp":"2026-11-10T09:15:02.000Z","context":{"plan":"pro"}},'
            . '{"flag_key":"a","variant":"on","reason":"TARGETING_MATCH","timestamp":"2026-11-10T09:15:02.000Z"}],'
            . '"counts":['
            . '{"flag_key":"a","variant":"on","hour":"2026-11-10T09:00:00Z","count":2},'
            . '{"flag_key":"a","variant":null,"hour":"2026-11-10T09:00:00Z","count":1}]}',
            $requests[0]['body'],
        );
    }

    public function testEmptyContextStaysAnObjectAndBothListsArePresent(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202]);

        $this->sender()->ship([self::line([], [self::exposure('u1', context: [])])]);

        self::assertSame(
            '{"exposures":[{"flag_key":"a","variant":"on","reason":"TARGETING_MATCH","timestamp":"2026-11-10T09:15:02.000Z","context":{}}],"counts":[]}',
            $this->server->requests()[0]['body'],
        );
    }

    public function testNothingToSendSendsNoRequest(): void
    {
        $result = $this->sender()->ship([self::line([]), 'not json', '', '[1,2]']);

        self::assertNull($result['remainder']);
        self::assertSame([], $this->server->requests());
    }

    public function testAggregatesOverSeveralLines(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202]);
        $lines = [
            self::line([['a', 'on', self::hour(9), 2], ['a', null, self::hour(9), 1]], [self::exposure('u1'), self::exposure('u2')]),
            self::line(
                [['a', 'on', self::hour(9), 3], ['a', 'on', self::hour(10), 1], ['b', 'x', self::hour(9), 600_000_000]],
                // u1 again in the same hour (later minute): deduplicated; u1 in hour 10: kept.
                [self::exposure('u1', timestamp: '2026-11-10T09:59:00.000Z'), self::exposure('u1', timestamp: '2026-11-10T10:00:00.000Z')],
            ),
            self::line([['a', null, self::hour(9), 4], ['b', 'x', self::hour(9), 600_000_000], ['bad'], ['a', 'on', 'x', 1]]),
        ];

        $this->sender()->ship($lines);

        $bodies = $this->bodies();
        self::assertCount(1, $bodies);
        $counts = array_map(static fn(\stdClass $c): array => [$c->flag_key, $c->variant, $c->hour, $c->count], $bodies[0]->counts);
        self::assertSame([
            ['a', 'on', '2026-11-10T09:00:00Z', 5],
            ['a', null, '2026-11-10T09:00:00Z', 5],
            ['a', 'on', '2026-11-10T10:00:00Z', 1],
            // A count over 1e9 becomes several items.
            ['b', 'x', '2026-11-10T09:00:00Z', 1_000_000_000],
            ['b', 'x', '2026-11-10T09:00:00Z', 200_000_000],
        ], $counts);
        $exposures = array_map(static fn(\stdClass $e): string => $e->timestamp, $bodies[0]->exposures);
        self::assertSame(['2026-11-10T09:15:02.000Z', '2026-11-10T09:15:02.000Z', '2026-11-10T10:00:00.000Z'], $exposures);
        foreach ($bodies[0]->exposures as $e) {
            self::assertObjectNotHasProperty('context_key', $e);
        }
    }

    public function testBatchesOfAtMost1000ItemsPerList(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202]);
        $exposures = [];
        for ($i = 0; $i < 1500; $i++) {
            $exposures[] = self::exposure('u' . $i);
        }
        $counts = [];
        for ($i = 0; $i < 2500; $i++) {
            $counts[] = ['f' . $i, 'on', self::hour(9), 1];
        }

        $this->sender()->ship([self::line($counts, $exposures)]);

        $sizes = array_map(static fn(\stdClass $b): array => [count($b->exposures), count($b->counts)], $this->bodies());
        self::assertSame([[1000, 1000], [500, 1000], [0, 500]], $sizes);
    }

    public function testBodyOver1MbIsSplitBeforeSending(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202]);
        $big = str_repeat('x', 400_000);
        $line = self::line([], [
            self::exposure('u1', context: ['blob' => $big]),
            self::exposure('u2', context: ['blob' => $big]),
            self::exposure('u3', context: ['blob' => $big]),
        ]);

        $result = $this->sender()->ship([$line]);

        self::assertNull($result['remainder']);
        $requests = $this->server->requests();
        self::assertCount(2, $requests);
        foreach ($requests as $request) {
            self::assertLessThanOrEqual(1_000_000, strlen($request['body']));
        }
        self::assertSame([1, 2], array_map(static fn(\stdClass $b): int => count($b->exposures), $this->bodies()));
    }

    public function testSingleOversizedItemIsDropped(): void
    {
        $result = $this->sender()->ship([self::line([], [self::exposure('u1', context: ['blob' => str_repeat('x', 1_000_001)])])]);

        self::assertSame(1, $result['dropped']);
        self::assertNull($result['remainder']);
        self::assertSame([], $this->server->requests());
    }

    public function testStatus413Splits(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 413], ['status' => 202]);

        $result = $this->sender()->ship([self::line([['a', 'on', self::hour(9), 1]], [self::exposure('u1')])]);

        self::assertNull($result['remainder']);
        $sizes = array_map(static fn(\stdClass $b): array => [count($b->exposures), count($b->counts)], $this->bodies());
        self::assertSame([[1, 1], [1, 0], [0, 1]], $sizes);
    }

    public function testStatus422DropsTheBatchAndContinues(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 422], ['status' => 202]);
        $counts = [];
        for ($i = 0; $i < 1500; $i++) {
            $counts[] = ['f' . $i, 'on', self::hour(9), 1];
        }

        $result = $this->sender()->ship([self::line($counts)]);

        self::assertSame(['dropped' => 1000, 'retryAfter' => null, 'remainder' => null, 'fatal' => null], $result);
        self::assertCount(2, $this->server->requests());
    }

    public function testStatus429ReturnsTheUnsentRemainderWithRetryAfter(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 429, 'headers' => ['Retry-After' => '7']]);
        $line = self::line([['a', 'on', self::hour(9), 2], ['a', null, self::hour(9), 1]], [self::exposure('u1', context: ['plan' => 'pro'])]);

        $result = $this->sender()->ship([$line]);

        self::assertSame(7.0, $result['retryAfter']);
        self::assertNull($result['fatal']);
        self::assertSame(
            '{"counts":[["a","on",' . self::hour(9) . ',2],["a",null,' . self::hour(9) . ',1]],'
            . '"exposures":[{"flag_key":"a","variant":"on","reason":"TARGETING_MATCH","timestamp":"2026-11-10T09:15:02.000Z","context_key":"u1","context":{"plan":"pro"}}],'
            . '"deduplicated":true}',
            $result['remainder'],
        );
        self::assertCount(1, $this->server->requests());
    }

    public function testStatus500ReturnsTheRemainderOfLaterBatchesToo(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202], ['status' => 500]);
        $counts = [];
        for ($i = 0; $i < 2500; $i++) {
            $counts[] = ['f' . $i, 'on', self::hour(9), 1];
        }

        $result = $this->sender()->ship([self::line($counts)]);

        self::assertSame(0.0, $result['retryAfter']);
        self::assertCount(2, $this->server->requests());
        $remainder = json_decode((string) $result['remainder'], true, 512, JSON_THROW_ON_ERROR);
        self::assertIsArray($remainder);
        self::assertCount(1500, $remainder['counts']);
        self::assertSame(['f1000', 'on', self::hour(9), 1], $remainder['counts'][0]);
        self::assertSame([], $remainder['exposures']);
    }

    public function testNetworkErrorReturnsTheRemainder(): void
    {
        $port = $this->server->url();
        $this->server->stop();
        $sender = new Sender(new Http($port, self::KEY));

        $result = $sender->ship([self::line([['a', 'on', self::hour(9), 1]])]);

        self::assertSame(0.0, $result['retryAfter']);
        self::assertNotNull($result['remainder']);
    }

    public function testRetryInsideASplitKeepsTheSecondHalfUnsent(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 413], ['status' => 500]);

        $result = $this->sender()->ship([self::line([['a', 'on', self::hour(9), 1], ['b', 'on', self::hour(9), 1]])]);

        self::assertCount(2, $this->server->requests());
        $remainder = json_decode((string) $result['remainder'], true, 512, JSON_THROW_ON_ERROR);
        self::assertIsArray($remainder);
        self::assertSame([['a', 'on', self::hour(9), 1], ['b', 'on', self::hour(9), 1]], $remainder['counts']);
    }

    public function testStatus401ReportsFatalAndStops(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 401]);
        $counts = [];
        for ($i = 0; $i < 2500; $i++) {
            $counts[] = ['f' . $i, 'on', self::hour(9), 1];
        }

        $result = $this->sender()->ship([self::line($counts)]);

        self::assertSame('401', $result['fatal']);
        self::assertNull($result['remainder']);
        self::assertNull($result['retryAfter']);
        self::assertCount(1, $this->server->requests());
    }

    public function testStatus403InsideASplitReportsFatalAndStops(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 413], ['status' => 403]);

        $result = $this->sender()->ship([self::line([['a', 'on', self::hour(9), 1], ['b', 'on', self::hour(9), 1]])]);

        self::assertSame('403', $result['fatal']);
        self::assertNull($result['remainder']);
        self::assertCount(2, $this->server->requests());
    }

    public function testDedupCallbackIsKeptAcrossShipments(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202], ['status' => 500], ['status' => 202]);
        $seen = [];
        $dedup = static function (string $key) use (&$seen): bool {
            if (isset($seen[$key])) {
                return true;
            }
            $seen[$key] = true;

            return false;
        };
        $sender = $this->sender($dedup);

        $sender->ship([self::line([], [self::exposure('u1')])]);
        $second = $sender->ship([self::line([], [self::exposure('u1', timestamp: '2026-11-10T09:40:00.000Z'), self::exposure('u2')])]);
        // The unsent remainder was already deduplicated: it is sent, not dropped as seen.
        $sender->ship([(string) $second['remainder'], self::line([], [self::exposure('u2', timestamp: '2026-11-10T09:50:00.000Z')])]);

        $sent = array_map(
            static fn(\stdClass $b): array => array_map(static fn(\stdClass $e): string => $e->timestamp, $b->exposures),
            $this->bodies(),
        );
        self::assertSame([
            ['2026-11-10T09:15:02.000Z'],
            ['2026-11-10T09:15:02.000Z'],
            ['2026-11-10T09:15:02.000Z'],
        ], $sent);
        self::assertCount(2, $seen);
    }
}
