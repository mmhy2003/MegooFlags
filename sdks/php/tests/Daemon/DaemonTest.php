<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Daemon;

use MegooFlags\Internal\Cache\CacheFiles;
use MegooFlags\Internal\Cache\State;
use MegooFlags\Internal\Engine\Compiler;
use MegooFlags\Internal\Events\Spool;
use MegooFlags\Internal\Transport\Http;
use MegooFlags\Internal\Upkeep;
use MegooFlags\Tests\Support\FakeServer;
use MegooFlags\Tests\Support\RulesetGen as G;
use PHPUnit\Framework\TestCase;
use RuntimeException;

/**
 * DaemonTest runs bin/megooflags-sync as a child process against the fake
 * server, with the timings shortened through the MEGOOFLAGS_INTERNAL_*
 * variables.
 */
final class DaemonTest extends TestCase
{
    private const KEY = 'mf_server_daemon-secret';

    private const BIN = __DIR__ . '/../../bin/megooflags-sync';

    private const EXIT_CONFIG = 78;

    private FakeServer $server;

    private string $dir;

    private CacheFiles $files;

    /** @var resource|null */
    private $process = null;

    private ?int $exitCode = null;

    protected function setUp(): void
    {
        $this->server = FakeServer::start(4);
        $this->dir = sys_get_temp_dir() . '/megooflags-daemon-' . bin2hex(random_bytes(6));
        mkdir($this->dir, 0o700);
        $this->files = new CacheFiles($this->dir . '/cache', self::KEY);
    }

    protected function tearDown(): void
    {
        if (is_resource($this->process)) {
            proc_terminate($this->process, 9);
            proc_close($this->process);
        }
        $this->server->stop();
        $output = $this->daemonOutput();
        self::removeTree($this->dir);
        self::assertStringNotContainsString(self::KEY, $output, 'the key never appears in the output');
        self::assertStringNotContainsString('daemon-secret', $output);
    }

    private static function removeTree(string $path): void
    {
        if (is_dir($path) && !is_link($path)) {
            foreach (scandir($path) ?: [] as $name) {
                if ($name !== '.' && $name !== '..') {
                    self::removeTree($path . '/' . $name);
                }
            }
            rmdir($path);
        } elseif (file_exists($path)) {
            unlink($path);
        }
    }

    /**
     * start runs the daemon with the base URL, cache directory and key in
     * the environment, plus $env and $args.
     *
     * @param list<string> $args
     * @param array<string, string> $env
     */
    private function start(array $args = [], array $env = [], ?string $key = self::KEY): void
    {
        $base = array_filter(getenv(), static fn(string $name): bool => !str_starts_with($name, 'MEGOOFLAGS_'), ARRAY_FILTER_USE_KEY);
        $base += [
            'MEGOOFLAGS_BASE_URL' => $this->server->url(),
            'MEGOOFLAGS_CACHE_DIR' => $this->files->dir(),
            'MEGOOFLAGS_INTERNAL_RETRY_BASE' => '0.05',
            'MEGOOFLAGS_INTERNAL_RETRY_MAX' => '0.1',
            'MEGOOFLAGS_INTERNAL_HEARTBEAT' => '0.2',
            'MEGOOFLAGS_INTERNAL_POLL_INTERVAL' => '0.2',
            'MEGOOFLAGS_INTERNAL_FLUSH_INTERVAL' => '0.3',
        ];
        if ($key !== null) {
            $base['MEGOOFLAGS_SDK_KEY'] = $key;
        }
        $process = proc_open(
            [PHP_BINARY, self::BIN, ...$args],
            [0 => ['file', '/dev/null', 'r'], 1 => ['file', $this->dir . '/stdout', 'a'], 2 => ['file', $this->dir . '/stderr', 'a']],
            $pipes,
            $this->dir,
            array_merge($base, $env),
        );
        if ($process === false) {
            throw new RuntimeException('cannot start the daemon');
        }
        $this->process = $process;
        $this->exitCode = null;
    }

    private function running(): bool
    {
        if ($this->exitCode !== null || !is_resource($this->process)) {
            return false;
        }
        $status = proc_get_status($this->process);
        if ($status['running']) {
            return true;
        }
        $this->exitCode = $status['exitcode'];

        return false;
    }

    private function waitExit(float $timeout = 5.0): int
    {
        $this->waitFor(fn(): bool => !$this->running(), $timeout, 'the daemon did not exit');

        return (int) $this->exitCode;
    }

    private function waitFor(\Closure $condition, float $timeout, string $message): void
    {
        $deadline = microtime(true) + $timeout;
        while (!$condition()) {
            if (microtime(true) > $deadline) {
                self::fail($message . "\n" . $this->daemonOutput());
            }
            usleep(20_000);
        }
    }

    private function stdout(): string
    {
        return (string) @file_get_contents($this->dir . '/stdout');
    }

    private function stderr(): string
    {
        return (string) @file_get_contents($this->dir . '/stderr');
    }

    private function daemonOutput(): string
    {
        return $this->stdout() . $this->stderr();
    }

    private function state(): State
    {
        return State::load($this->files->statePath());
    }

    private static function raw(string $variation = 'on'): string
    {
        return (string) json_encode(G::ruleset([G::flag('f', ['fallthrough' => G::serve($variation)])]), JSON_UNESCAPED_SLASHES);
    }

    /**
     * stream is a 200 event stream sending $events, then a ping every
     * 100 ms for 10 s.
     *
     * @return array{status: int, headers: array<string, string>, body: string, chunks: list<array{float, string}>, delay?: float}
     */
    private static function stream(string $events, float $delay = 0.0): array
    {
        $response = [
            'status' => 200,
            'headers' => ['Content-Type' => 'text/event-stream'],
            'body' => $events,
            'chunks' => array_fill(0, 100, [0.1, ":\n"]),
        ];
        if ($delay > 0) {
            $response['delay'] = $delay;
        }

        return $response;
    }

    private static function put(string $etag, string $raw): string
    {
        return "event: put\nid: " . $etag . "\ndata: " . $raw . "\n\n";
    }

    /** @return list<array{method: string, path: string, query: string, headers: array<string, string>, body: string}> */
    private function requests(string $method, string $path): array
    {
        return array_values(array_filter(
            $this->server->requests(),
            static fn(array $r): bool => $r['method'] === $method && $r['path'] === $path,
        ));
    }

    private function spool(int $count, string $contextKey = 'u1'): void
    {
        $line = (string) json_encode([
            'counts' => [['f', 'on', gmmktime(9, 0, 0, 11, 10, 2026), $count]],
            'exposures' => [['flag_key' => 'f', 'variant' => 'on', 'reason' => 'STATIC', 'timestamp' => '2026-11-10T09:15:02.000Z', 'context_key' => $contextKey]],
        ]);
        self::assertTrue((new Spool($this->files->spoolPath()))->append($line));
    }

    /** @return list<array{exposures: list<array<string, mixed>>, counts: list<array<string, mixed>>}> */
    private function eventBodies(): array
    {
        $bodies = [];
        foreach ($this->requests('POST', '/sdk/v1/events') as $request) {
            /** @var array{exposures: list<array<string, mixed>>, counts: list<array<string, mixed>>} $body */
            $body = json_decode($request['body'], true, 512, JSON_THROW_ON_ERROR);
            $bodies[] = $body;
        }

        return $bodies;
    }

    public function testAPutWritesBothCacheFilesAndTheStateEtag(): void
    {
        $this->server->respond('GET', '/sdk/v1/stream', self::stream(self::put('"e1"', self::raw())));

        $this->start();

        $this->waitFor(fn(): bool => $this->state()->etag === '"e1"', 5.0, 'no state etag');
        self::assertEquals(['etag' => '"e1"', 'ruleset' => json_decode(self::raw(), false)], $this->files->loadJson());
        self::assertSame(
            ['etag' => '"e1"', 'source' => 'network', 'ruleset' => Compiler::compileJson(self::raw())],
            $this->files->loadCompiled(),
        );
        $request = $this->requests('GET', '/sdk/v1/stream')[0];
        self::assertSame('Bearer ' . self::KEY, $request['headers']['authorization']);
        self::assertSame('text/event-stream', $request['headers']['accept']);
        self::assertSame('no-cache', $request['headers']['cache-control']);
        self::assertArrayNotHasKey('last-event-id', $request['headers']);
        self::assertSame('', $request['query']);
        self::assertTrue($this->running());
    }

    public function testTheStateHeartbeatKeepsUpkeepFromBeingDue(): void
    {
        $this->server->respond('GET', '/sdk/v1/stream', self::stream(self::put('"e1"', self::raw())));
        $this->start();
        $this->waitFor(fn(): bool => $this->state()->etag === '"e1"', 5.0, 'no state etag');
        $checked = $this->state()->checkedAt;

        $this->waitFor(fn(): bool => $this->state()->checkedAt > $checked, 5.0, 'checked_at was not refreshed');

        self::assertEqualsWithDelta(microtime(true), $this->state()->checkedAt, 1.0);
        $upkeep = new Upkeep($this->files, new Http($this->server->url(), self::KEY), 15.0);
        self::assertFalse($upkeep->acquireIfDue(microtime(true)), 'requests skip upkeep while the daemon runs');
    }

    public function testAnInvalidPutKeepsThePreviousFiles(): void
    {
        $response = self::stream(self::put('"e1"', self::raw()));
        $response['chunks'] = [[0.3, self::put('"e2"', '{"schema_version": 99}')], ...$response['chunks']];
        $this->server->respond('GET', '/sdk/v1/stream', $response);

        $this->start();

        $this->waitFor(fn(): bool => str_contains($this->stderr(), 'invalid ruleset'), 5.0, 'no warning for the invalid put');
        self::assertSame('"e1"', $this->state()->etag);
        self::assertSame('"e1"', $this->files->loadJson()['etag'] ?? null);
        self::assertSame('"e1"', $this->files->loadCompiled()['etag'] ?? null);
        self::assertTrue($this->running(), 'the stream continues');
        self::assertCount(1, $this->requests('GET', '/sdk/v1/stream'));
    }

    public function testAReconnectSendsLastEventId(): void
    {
        $this->server->respond(
            'GET',
            '/sdk/v1/stream',
            ['status' => 200, 'headers' => ['Content-Type' => 'text/event-stream'], 'body' => self::put('"e1"', self::raw())],
            self::stream(''),
        );

        $this->start();

        $this->waitFor(fn(): bool => count($this->requests('GET', '/sdk/v1/stream')) >= 2, 5.0, 'no reconnect');
        self::assertSame('"e1"', $this->requests('GET', '/sdk/v1/stream')[1]['headers']['last-event-id'] ?? null);
    }

    public function testASilentStreamIsDeadAndReconnects(): void
    {
        $silent = ['status' => 200, 'headers' => ['Content-Type' => 'text/event-stream'], 'body' => self::put('"e1"', self::raw()), 'chunks' => [[5, '']]];
        $this->server->respond('GET', '/sdk/v1/stream', $silent, self::stream(''));

        $this->start([], ['MEGOOFLAGS_INTERNAL_DEAD_TIMEOUT' => '0.5']);

        $this->waitFor(fn(): bool => count($this->requests('GET', '/sdk/v1/stream')) >= 2, 4.0, 'no reconnect after 0.5 s of silence');
        self::assertStringContainsString('dead connection', $this->stderr());
    }

    public function testAStream429WaitsForRetryAfter(): void
    {
        $this->server->respond('GET', '/sdk/v1/stream', ['status' => 429, 'headers' => ['Retry-After' => '1']], self::stream(''));

        $started = microtime(true);
        $this->start();

        $this->waitFor(fn(): bool => count($this->requests('GET', '/sdk/v1/stream')) >= 2, 5.0, 'no reconnect');
        self::assertGreaterThanOrEqual(1.0, microtime(true) - $started);
        self::assertTrue($this->running());
    }

    public function testTwoStreamFailuresStartPollingAndAReconnectStopsIt(): void
    {
        $this->server->respond(
            'GET',
            '/sdk/v1/stream',
            ['status' => 500],
            ['status' => 500],
            self::stream(self::put('"e2"', self::raw('off')), 1.5),
        );
        $this->server->respond(
            'GET',
            '/sdk/v1/ruleset',
            ['status' => 200, 'headers' => ['ETag' => '"e1"'], 'body' => self::raw()],
            ['status' => 304],
        );

        $this->start();

        $this->waitFor(fn(): bool => $this->state()->etag === '"e2"', 8.0, 'the stream did not reconnect');
        $polls = $this->requests('GET', '/sdk/v1/ruleset');
        self::assertGreaterThanOrEqual(2, count($polls));
        self::assertArrayNotHasKey('if-none-match', $polls[0]['headers']);
        self::assertSame('"e1"', $polls[1]['headers']['if-none-match'] ?? null);
        usleep(300_000);
        $after = count($this->requests('GET', '/sdk/v1/ruleset'));
        usleep(1_000_000);
        self::assertCount($after, $this->requests('GET', '/sdk/v1/ruleset'), 'polling stopped on reconnect');
        self::assertSame('"e2"', $this->files->loadCompiled()['etag'] ?? null);
    }

    public function testShipsTheSpoolAndDeduplicatesExposuresWithinTheHour(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 304]);
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202]);
        $this->spool(3);

        $this->start([], ['MEGOOFLAGS_DISABLE_STREAM' => '1']);

        $this->waitFor(fn(): bool => count($this->eventBodies()) >= 1, 5.0, 'the spool was not shipped');
        $first = $this->eventBodies()[0];
        self::assertSame([['flag_key' => 'f', 'variant' => 'on', 'reason' => 'STATIC', 'timestamp' => '2026-11-10T09:15:02.000Z']], $first['exposures']);
        self::assertSame([['flag_key' => 'f', 'variant' => 'on', 'hour' => '2026-11-10T09:00:00Z', 'count' => 3]], $first['counts']);
        $request = $this->requests('POST', '/sdk/v1/events')[0];
        self::assertSame('Bearer ' . self::KEY, $request['headers']['authorization']);

        $this->spool(2);

        $this->waitFor(fn(): bool => count($this->eventBodies()) >= 2, 5.0, 'the second line was not shipped');
        $second = $this->eventBodies()[1];
        self::assertSame([], $second['exposures'], 'the same exposure in the same hour is not sent again');
        self::assertSame([['flag_key' => 'f', 'variant' => 'on', 'hour' => '2026-11-10T09:00:00Z', 'count' => 2]], $second['counts']);
        self::assertSame([], glob($this->files->dir() . '/*.sending'));
        self::assertCount(0, $this->requests('GET', '/sdk/v1/stream'));
    }

    public function testDisableStreamPollsWithIfNoneMatch(): void
    {
        $this->server->respond(
            'GET',
            '/sdk/v1/ruleset',
            ['status' => 200, 'headers' => ['ETag' => '"e1"'], 'body' => self::raw()],
            ['status' => 304],
        );

        $this->start(['--disable-stream']);

        $this->waitFor(fn(): bool => count($this->requests('GET', '/sdk/v1/ruleset')) >= 2, 5.0, 'no second poll');
        self::assertSame('"e1"', $this->requests('GET', '/sdk/v1/ruleset')[1]['headers']['if-none-match'] ?? null);
        self::assertSame('"e1"', $this->state()->etag);
        self::assertSame('"e1"', $this->files->loadCompiled()['etag'] ?? null);
    }

    public function testStatus401WritesTheFatalMarkerAndExits78(): void
    {
        $this->server->respond('GET', '/sdk/v1/stream', ['status' => 401]);

        $this->start();

        self::assertSame(self::EXIT_CONFIG, $this->waitExit());
        self::assertSame('401', $this->state()->fatal);
        self::assertStringContainsString('SDK key refused (401)', $this->stderr());
        self::assertCount(1, $this->server->requests());
    }

    public function testStatus403OnAPollWritesTheFatalMarkerAndExits78(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 403]);

        $this->start(['--disable-stream']);

        self::assertSame(self::EXIT_CONFIG, $this->waitExit());
        self::assertSame('403', $this->state()->fatal);
    }

    public function testARevokedEventWritesTheFatalMarkerAndExits78(): void
    {
        $response = self::stream(self::put('"e1"', self::raw()));
        $response['chunks'] = [[0.3, "event: revoked\ndata: {}\n\n"], ...$response['chunks']];
        $this->server->respond('GET', '/sdk/v1/stream', $response);

        $this->start();

        self::assertSame(self::EXIT_CONFIG, $this->waitExit());
        self::assertSame('revoked', $this->state()->fatal);
        self::assertSame('"e1"', $this->files->loadCompiled()['etag'] ?? null, 'the cached ruleset stays');
        self::assertStringContainsString('SDK key refused (revoked)', $this->stderr());
    }

    public function testAFatalMarkerAtStartExits78WithoutARequest(): void
    {
        (new State(fatal: '403'))->save($this->files->statePath());

        $this->start();

        self::assertSame(self::EXIT_CONFIG, $this->waitExit());
        self::assertSame([], $this->server->requests());
    }

    public function testAClientKeyExits78WithoutARequest(): void
    {
        $this->start([], [], 'mf_client_daemon-secret');

        self::assertSame(self::EXIT_CONFIG, $this->waitExit());
        self::assertStringContainsString('client SDK key', $this->stderr());
        self::assertSame([], $this->server->requests());
    }

    public function testAMissingKeyOrCacheDirectoryExits78(): void
    {
        $this->start([], [], null);
        self::assertSame(self::EXIT_CONFIG, $this->waitExit());
        self::assertStringContainsString('MEGOOFLAGS_SDK_KEY', $this->stderr());

        $this->start([], ['MEGOOFLAGS_CACHE_DIR' => '']);
        self::assertSame(self::EXIT_CONFIG, $this->waitExit());
        self::assertStringContainsString('cacheDir', $this->stderr());
        self::assertSame([], $this->server->requests());
    }

    public function testAnUnknownFlagExits78(): void
    {
        $this->start(['--nope']);

        self::assertSame(self::EXIT_CONFIG, $this->waitExit());
        self::assertStringContainsString('--nope', $this->stderr());
        self::assertSame([], $this->server->requests());
    }

    public function testOnceFetchesShipsAndExits0(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 200, 'headers' => ['ETag' => '"e1"'], 'body' => self::raw()]);
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202]);
        $this->spool(3);

        // The flags win over the environment.
        $this->start(
            ['--once', '--base-url=' . $this->server->url(), '--cache-dir', $this->files->dir()],
            ['MEGOOFLAGS_BASE_URL' => 'http://127.0.0.1:1', 'MEGOOFLAGS_CACHE_DIR' => $this->dir . '/elsewhere'],
        );

        self::assertSame(0, $this->waitExit(10.0));
        self::assertArrayNotHasKey('if-none-match', $this->requests('GET', '/sdk/v1/ruleset')[0]['headers']);
        self::assertSame('"e1"', $this->state()->etag);
        self::assertSame('"e1"', $this->files->loadJson()['etag'] ?? null);
        self::assertSame('"e1"', $this->files->loadCompiled()['etag'] ?? null);
        self::assertCount(1, $this->eventBodies());
        self::assertSame(3, $this->eventBodies()[0]['counts'][0]['count']);
        self::assertSame([], $this->requests('GET', '/sdk/v1/stream'));
    }

    public function testOnceWithANetworkErrorExits1(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 500]);

        $this->start(['--once']);

        self::assertSame(1, $this->waitExit(10.0));
        self::assertSame(1, $this->state()->failures);
    }

    public function testSigtermShipsTheSpoolAndExits0(): void
    {
        if (!extension_loaded('pcntl')) {
            self::markTestSkipped('pcntl is not loaded');
        }
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 304]);
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202]);
        $this->start(['--disable-stream'], ['MEGOOFLAGS_INTERNAL_FLUSH_INTERVAL' => '60']);
        $this->waitFor(fn(): bool => is_file($this->files->statePath()), 5.0, 'the daemon did not start');
        $this->spool(4);

        self::assertIsResource($this->process);
        proc_terminate($this->process, 15);

        self::assertSame(0, $this->waitExit());
        self::assertCount(1, $this->eventBodies());
        self::assertSame(4, $this->eventBodies()[0]['counts'][0]['count']);
    }

    public function testHelpPrintsTheOptionsAndExits0(): void
    {
        $this->start(['--help'], [], null);

        self::assertSame(0, $this->waitExit());
        foreach (
            [
                '--base-url', '--cache-dir', '--poll-interval', '--flush-interval', '--disable-stream', '--once', '--help',
                'MEGOOFLAGS_SDK_KEY', 'MEGOOFLAGS_BASE_URL', 'MEGOOFLAGS_CACHE_DIR', '78',
            ] as $text
        ) {
            self::assertStringContainsString($text, $this->stdout());
        }
        self::assertSame([], $this->server->requests());
    }
}
