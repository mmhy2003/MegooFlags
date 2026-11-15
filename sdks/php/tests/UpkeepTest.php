<?php

declare(strict_types=1);

namespace MegooFlags\Tests;

use MegooFlags\Internal\Cache\CacheFiles;
use MegooFlags\Internal\Cache\State;
use MegooFlags\Internal\Engine\Compiler;
use MegooFlags\Internal\Events\Spool;
use MegooFlags\Internal\Transport\Http;
use MegooFlags\Internal\Upkeep;
use MegooFlags\Tests\Support\FakeServer;
use MegooFlags\Tests\Support\RulesetGen as G;
use PHPUnit\Framework\TestCase;

final class UpkeepTest extends TestCase
{
    private const KEY = 'mf_server_upkeep-secret';

    private const NOW = 1_800_000_000.0;

    private FakeServer $server;

    private string $dir;

    private CacheFiles $files;

    protected function setUp(): void
    {
        $this->server = FakeServer::start();
        $this->dir = sys_get_temp_dir() . '/megooflags-upkeep-' . bin2hex(random_bytes(6));
        $this->files = new CacheFiles($this->dir, self::KEY);
    }

    protected function tearDown(): void
    {
        $this->server->stop();
        foreach (glob($this->dir . '/{,.}*', GLOB_BRACE) ?: [] as $file) {
            if (is_file($file)) {
                unlink($file);
            }
        }
        if (is_dir($this->dir)) {
            rmdir($this->dir);
        }
    }

    private function upkeep(float $pollInterval = 30.0): Upkeep
    {
        return new Upkeep($this->files, new Http($this->server->url(), self::KEY), $pollInterval, static fn(): float => 1.0);
    }

    private function state(): State
    {
        return State::load($this->files->statePath());
    }

    private function saveState(State $state): void
    {
        $state->save($this->files->statePath());
    }

    private static function raw(string $variation = 'on'): string
    {
        return (string) json_encode(G::ruleset([G::flag('f', ['fallthrough' => G::serve($variation)])]), JSON_UNESCAPED_SLASHES);
    }

    /** @return list<array{method: string, path: string, query: string, headers: array<string, string>, body: string}> */
    private function requests(string $method): array
    {
        return array_values(array_filter($this->server->requests(), static fn(array $r): bool => $r['method'] === $method));
    }

    private function spoolLine(string $contextKey = 'u1'): string
    {
        return (string) json_encode([
            'counts' => [['f', 'on', gmmktime(9, 0, 0, 11, 10, 2026), 3]],
            'exposures' => [['flag_key' => 'f', 'variant' => 'on', 'reason' => 'STATIC', 'timestamp' => '2026-11-10T09:15:02.000Z', 'context_key' => $contextKey]],
        ]);
    }

    public function testDueWithoutAStateFileAndHoldsTheLock(): void
    {
        $upkeep = $this->upkeep();

        self::assertTrue($upkeep->acquireIfDue(self::NOW));
        self::assertFalse($this->upkeep()->acquireIfDue(self::NOW), 'the lock is held');

        $upkeep->release();
        self::assertTrue($this->upkeep()->acquireIfDue(self::NOW));
    }

    public function testDueOnlyWhenTheStateIsOlderThanThePollInterval(): void
    {
        $this->saveState(new State(checkedAt: self::NOW - 29.0));
        self::assertFalse($this->upkeep()->acquireIfDue(self::NOW));

        $this->saveState(new State(checkedAt: self::NOW - 30.0));
        self::assertTrue($this->upkeep()->acquireIfDue(self::NOW));
    }

    public function testNotDueBeforeNextAttempt(): void
    {
        $this->saveState(new State(nextAttempt: self::NOW + 1.0));
        self::assertFalse($this->upkeep()->acquireIfDue(self::NOW));

        $this->saveState(new State(nextAttempt: self::NOW));
        self::assertTrue($this->upkeep()->acquireIfDue(self::NOW));
    }

    public function testNotDueWithAFatalMarker(): void
    {
        $this->saveState(new State(fatal: '401'));

        self::assertFalse($this->upkeep()->acquireIfDue(self::NOW));
    }

    public function testStatus200WritesBothCacheFilesAndTheStateEtag(): void
    {
        $this->saveState(new State(failures: 3));
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 200, 'headers' => ['ETag' => '"v1"'], 'body' => self::raw()]);

        $this->upkeep()->run(self::NOW);

        $request = $this->requests('GET')[0];
        self::assertArrayNotHasKey('if-none-match', $request['headers']);
        self::assertSame('Bearer ' . self::KEY, $request['headers']['authorization']);
        self::assertEquals(['etag' => '"v1"', 'ruleset' => json_decode(self::raw(), false)], $this->files->loadJson());
        self::assertSame(
            ['etag' => '"v1"', 'source' => 'network', 'ruleset' => Compiler::compileJson(self::raw())],
            $this->files->loadCompiled(),
        );
        $state = $this->state();
        self::assertSame('"v1"', $state->etag);
        self::assertSame(self::NOW, $state->checkedAt);
        self::assertSame(0, $state->failures);
        self::assertSame(0.0, $state->nextAttempt);
        self::assertNull($state->fatal);
        self::assertTrue($this->upkeep()->acquireIfDue(self::NOW + 30.0), 'run releases the lock');
    }

    public function testStatus304KeepsTheCacheFiles(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 200, 'headers' => ['ETag' => '"v1"'], 'body' => self::raw()]);
        $this->upkeep()->run(self::NOW);
        $json = file_get_contents($this->files->jsonPath());
        $compiled = file_get_contents($this->files->compiledPath());
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 304]);

        $this->upkeep()->run(self::NOW + 30.0);

        self::assertSame('"v1"', $this->requests('GET')[1]['headers']['if-none-match']);
        self::assertSame($json, file_get_contents($this->files->jsonPath()));
        self::assertSame($compiled, file_get_contents($this->files->compiledPath()));
        $state = $this->state();
        self::assertSame('"v1"', $state->etag);
        self::assertSame(self::NOW + 30.0, $state->checkedAt);
    }

    public function testInvalidRulesetIsAFailureAndKeepsTheCacheFiles(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 200, 'headers' => ['ETag' => '"v1"'], 'body' => '{"schema_version": 99}']);

        $this->upkeep()->run(self::NOW);

        self::assertFileDoesNotExist($this->files->jsonPath());
        self::assertFileDoesNotExist($this->files->compiledPath());
        self::assertSame(1, $this->state()->failures);
        self::assertSame('', $this->state()->etag);
    }

    public function testStatus401WritesTheFatalMarkerAndLaterRunsDoNothing(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 401]);
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202]);
        $spool = new Spool($this->files->spoolPath());
        $spool->append($this->spoolLine());

        $this->upkeep()->run(self::NOW);

        self::assertSame('401', $this->state()->fatal);
        self::assertCount(1, $this->server->requests());
        self::assertFalse($this->upkeep()->acquireIfDue(self::NOW + 60.0));

        $this->upkeep()->run(self::NOW + 60.0);
        self::assertCount(1, $this->server->requests());
        self::assertSame('401', $this->state()->fatal);
    }

    public function testStatus403WritesTheFatalMarker(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 403]);

        $this->upkeep()->run(self::NOW);

        self::assertSame('403', $this->state()->fatal);
    }

    public function testStatus500RaisesFailuresAndNextAttempt(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 500]);

        $this->upkeep()->run(self::NOW);
        $state = $this->state();
        self::assertSame(1, $state->failures);
        self::assertSame(self::NOW + 2.0, $state->nextAttempt);
        self::assertSame(self::NOW, $state->checkedAt);
        self::assertNull($state->fatal);

        $this->upkeep()->run(self::NOW + 2.0);
        $state = $this->state();
        self::assertSame(2, $state->failures);
        self::assertSame(self::NOW + 2.0 + 4.0, $state->nextAttempt);
    }

    public function testStatus429UsesRetryAfterWhenLonger(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 429, 'headers' => ['Retry-After' => '30']]);

        $this->upkeep()->run(self::NOW);

        self::assertSame(self::NOW + 30.0, $this->state()->nextAttempt);
        self::assertSame(1, $this->state()->failures);
    }

    public function testNetworkErrorRaisesFailures(): void
    {
        $url = $this->server->url();
        $this->server->stop();
        $upkeep = new Upkeep($this->files, new Http($url, self::KEY), 30.0, static fn(): float => 1.0);

        $upkeep->run(self::NOW);

        self::assertSame(1, $this->state()->failures);
        self::assertSame(self::NOW + 2.0, $this->state()->nextAttempt);
    }

    public function testShipsTheSpoolAndDeletesTheClaimedFile(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 304]);
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202]);
        $spool = new Spool($this->files->spoolPath());
        $spool->append($this->spoolLine('u1'));
        $spool->append($this->spoolLine('u1'));

        $this->upkeep()->run(self::NOW);

        $posts = $this->requests('POST');
        self::assertCount(1, $posts);
        $body = json_decode($posts[0]['body'], true, 512, JSON_THROW_ON_ERROR);
        self::assertIsArray($body);
        self::assertCount(1, $body['exposures']);
        self::assertSame(6, $body['counts'][0]['count']);
        self::assertSame([], glob($this->dir . '/*.events.*') ?: []);
        self::assertSame(0, $this->state()->failures);
    }

    public function testUnsentRemainderIsAppendedBackToTheSpool(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 304]);
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 500], ['status' => 202]);
        (new Spool($this->files->spoolPath()))->append($this->spoolLine());

        $this->upkeep()->run(self::NOW);

        $lines = file($this->files->spoolPath(), FILE_IGNORE_NEW_LINES) ?: [];
        self::assertCount(1, $lines);
        $line = json_decode($lines[0], true, 512, JSON_THROW_ON_ERROR);
        self::assertIsArray($line);
        self::assertSame([['f', 'on', gmmktime(9, 0, 0, 11, 10, 2026), 3]], $line['counts']);
        self::assertSame('u1', $line['exposures'][0]['context_key']);
        self::assertSame([$this->files->spoolPath()], glob($this->dir . '/*.events.*') ?: []);
        $state = $this->state();
        self::assertSame(1, $state->failures);
        self::assertSame(self::NOW + 2.0, $state->nextAttempt);
        self::assertSame(self::NOW, $state->checkedAt);

        // The next run sends what was kept.
        $this->upkeep()->run(self::NOW + 2.0);
        self::assertCount(2, $this->requests('POST'));
        self::assertSame($this->requests('POST')[0]['body'], $this->requests('POST')[1]['body']);
        self::assertSame([], glob($this->dir . '/*.events.*') ?: []);
        self::assertSame(0, $this->state()->failures);
    }

    public function testEvents429SetsNextAttemptFromRetryAfter(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 304]);
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 429, 'headers' => ['Retry-After' => '20']]);
        (new Spool($this->files->spoolPath()))->append($this->spoolLine());

        $this->upkeep()->run(self::NOW);

        self::assertSame(self::NOW + 20.0, $this->state()->nextAttempt);
        self::assertFileExists($this->files->spoolPath());
    }

    public function testEvents401WritesTheFatalMarkerAndDropsTheSpool(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 304]);
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 401]);
        (new Spool($this->files->spoolPath()))->append($this->spoolLine());

        $this->upkeep()->run(self::NOW);

        self::assertSame('401', $this->state()->fatal);
        self::assertSame([], glob($this->dir . '/*.events.*') ?: []);
    }

    public function testRunWhileAnotherProcessHoldsTheLockDoesNothing(): void
    {
        $holder = $this->upkeep();
        self::assertTrue($holder->acquireIfDue(self::NOW));

        $this->upkeep()->run(self::NOW);

        self::assertSame([], $this->server->requests());
        self::assertFileDoesNotExist($this->files->statePath());
    }

    public function testKeyIsNeverWrittenToTheCacheDirectory(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 500]);
        (new Spool($this->files->spoolPath()))->append($this->spoolLine());
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 500]);

        $this->upkeep()->run(self::NOW);

        foreach (glob($this->dir . '/*') ?: [] as $file) {
            self::assertStringNotContainsString(self::KEY, $file);
            self::assertStringNotContainsString(self::KEY, (string) file_get_contents($file));
        }
    }
}
