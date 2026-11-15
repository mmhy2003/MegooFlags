<?php

declare(strict_types=1);

namespace MegooFlags\Tests\E2E;

use MegooFlags\Config;
use MegooFlags\Internal\Cache\CacheFiles;
use MegooFlags\Internal\Cache\State;
use MegooFlags\Provider;
use OpenFeature\implementation\flags\EvaluationContext;
use OpenFeature\OpenFeatureAPI;
use PHPUnit\Framework\TestCase;
use RuntimeException;

/**
 * EndToEndTest runs the provider and the sync daemon against a running
 * MegooFlags stack. Set MEGOOFLAGS_E2E_URL (for example
 * http://localhost:8080) and MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh
 * prints one) to run it: `composer e2e`. Without both, it is skipped.
 *
 * A new Provider object stands for each PHP request: they share the cache
 * directory, as the requests of one application do.
 */
final class EndToEndTest extends TestCase
{
    private const REQUEST_TIMEOUT = 10;

    private const FLUSH_INTERVAL = 10.0;

    private const BIN = __DIR__ . '/../../bin/megooflags-sync';

    private string $base = '';

    private string $token = '';

    private ?string $project = null;

    private string $dir = '';

    /** @var resource|null */
    private $daemon = null;

    private string $sdkKey = '';

    protected function setUp(): void
    {
        $this->base = rtrim((string) getenv('MEGOOFLAGS_E2E_URL'), '/');
        $this->token = (string) getenv('MEGOOFLAGS_API_TOKEN');
        if ($this->base === '' || $this->token === '') {
            self::markTestSkipped('MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are not set');
        }
        $project = 'sdkphp-' . bin2hex(random_bytes(4));
        $this->dir = sys_get_temp_dir() . '/megooflags-e2e-' . bin2hex(random_bytes(6));
        mkdir($this->dir, 0o700);
        $this->must(201, 'POST', '/api/projects', [
            'key' => $project,
            'name' => $project,
            'environments' => [['key' => 'e2e', 'name' => 'E2E']],
        ]);
        $this->project = $project;
    }

    protected function tearDown(): void
    {
        if (is_resource($this->daemon)) {
            proc_terminate($this->daemon, 9);
            proc_close($this->daemon);
        }
        if ($this->project !== null) {
            $this->call('DELETE', '/api/projects/' . $this->project);
        }
        if ($this->dir !== '') {
            $output = $this->daemonOutput();
            self::removeTree($this->dir);
            if ($this->sdkKey !== '') {
                self::assertStringNotContainsString($this->sdkKey, $output, 'the daemon output contains the key');
            }
        }
    }

    public function testEndToEnd(): void
    {
        $flagPath = '/api/projects/' . $this->project . '/flags/e2e-flag/environments/e2e';
        $this->sdkKey = $this->createFlagAndKey($flagPath);
        $cacheDir = $this->dir . '/cache';

        // Check 1: the first request fetches the ruleset (cold start) and evaluates it.
        $first = $this->provider($this->base, $cacheDir);
        self::assertTrue(self::evaluate($first, false), 'e2e-flag is not true on the cold start');
        $first->flush();

        // Check 2: with the daemon following the stream, a toggle reaches new requests within 5 s.
        $this->startDaemon($cacheDir);
        $this->must(200, 'POST', $flagPath . '/toggle', ['enabled' => false]);
        $this->pollUntil('e2e-flag false after the toggle', 5.0, 0.05, function () use ($cacheDir): bool {
            $request = $this->provider($this->base, $cacheDir);
            $value = self::evaluate($request, true);
            $request->flush();

            return $value === false;
        });

        // Check 3: evaluation counts are spooled by the requests and shipped by the daemon.
        for ($i = 0; $i < 20; $i++) {
            $request = $this->provider($this->base, $cacheDir);
            self::evaluate($request, false);
            $request->flush();
        }
        $this->stopDaemon();
        $total = 0;
        $this->pollUntil('evaluations total >= 21', 15.0, 0.5, function () use ($flagPath, &$total): bool {
            $total = $this->evaluationsTotal($flagPath);

            return $total >= 21;
        });

        // Check 4: with the API unreachable, a new request serves the cache.
        $offline = $this->provider('http://127.0.0.1:' . self::closedPort(), $cacheDir, 1.0);
        self::assertFalse(self::evaluate($offline, true), 'e2e-flag is not false from the cache');

        // Check 5: without the daemon, the request whose upkeep is due refreshes the cache and ships the spool.
        $this->must(200, 'POST', $flagPath . '/toggle', ['enabled' => true]);
        $statePath = (new CacheFiles($cacheDir, $this->sdkKey))->statePath();
        $state = State::load($statePath);
        $state->checkedAt = 0.0;
        $state->save($statePath);
        $request = $this->provider($this->base, $cacheDir);
        self::evaluate($request, false);
        $request->flush();
        self::assertTrue(self::evaluate($this->provider($this->base, $cacheDir), false), 'e2e-flag is not true after the upkeep');
        $before = $total;
        $this->pollUntil('evaluations total > ' . $before, 15.0, 0.5, fn(): bool => $this->evaluationsTotal($flagPath) > $before);
    }

    private function provider(string $baseUrl, string $cacheDir, float $initTimeout = 5.0): Provider
    {
        return new Provider(new Config(
            sdkKey: $this->sdkKey,
            baseUrl: $baseUrl,
            cacheDir: $cacheDir,
            initTimeout: $initTimeout,
            flushInterval: self::FLUSH_INTERVAL,
        ));
    }

    /** evaluate registers the provider and evaluates e2e-flag for user-1. */
    private static function evaluate(Provider $provider, bool $default): bool
    {
        $api = OpenFeatureAPI::getInstance();
        $api->setProvider($provider);

        return $api->getClient('e2e')->getBooleanValue('e2e-flag', $default, new EvaluationContext('user-1'));
    }

    /** createFlagAndKey creates e2e-flag, turns it on and returns a new server key. */
    private function createFlagAndKey(string $flagPath): string
    {
        $this->must(201, 'POST', '/api/projects/' . $this->project . '/flags', ['key' => 'e2e-flag', 'name' => 'E2E', 'type' => 'boolean']);
        $this->must(200, 'POST', $flagPath . '/toggle', ['enabled' => true]);
        $created = $this->must(201, 'POST', '/api/projects/' . $this->project . '/environments/e2e/sdk-keys', ['name' => 'php-e2e', 'kind' => 'server']);
        $key = $created['key'] ?? null;
        self::assertIsString($key, 'POST sdk-keys returned no key');

        return $key;
    }

    private function evaluationsTotal(string $flagPath): int
    {
        $total = $this->must(200, 'GET', $flagPath . '/evaluations?range=24h')['total'] ?? null;

        return is_int($total) ? $total : -1;
    }

    /** startDaemon runs bin/megooflags-sync on the cache directory, with its output in files. */
    private function startDaemon(string $cacheDir): void
    {
        $env = array_filter(getenv(), static fn(string $name): bool => !str_starts_with($name, 'MEGOOFLAGS_'), ARRAY_FILTER_USE_KEY);
        $env += [
            'MEGOOFLAGS_SDK_KEY' => $this->sdkKey,
            'MEGOOFLAGS_BASE_URL' => $this->base,
            'MEGOOFLAGS_CACHE_DIR' => $cacheDir,
            'MEGOOFLAGS_FLUSH_INTERVAL' => (string) self::FLUSH_INTERVAL,
        ];
        $process = proc_open(
            [PHP_BINARY, self::BIN],
            [0 => ['file', '/dev/null', 'r'], 1 => ['file', $this->dir . '/daemon.out', 'a'], 2 => ['file', $this->dir . '/daemon.err', 'a']],
            $pipes,
            $this->dir,
            $env,
        );
        if ($process === false) {
            throw new RuntimeException('cannot start bin/megooflags-sync');
        }
        $this->daemon = $process;
    }

    /**
     * stopDaemon sends SIGTERM, which ships the spool once more; without
     * pcntl the daemon cannot catch it, so the test first waits one flush
     * interval for the regular shipment.
     */
    private function stopDaemon(): void
    {
        $process = $this->daemon;
        self::assertIsResource($process);
        $status = proc_get_status($process);
        self::assertTrue($status['running'], "the daemon exited early\n" . $this->daemonOutput());
        $catchesSignals = extension_loaded('pcntl');
        if (!$catchesSignals) {
            usleep((int) ((self::FLUSH_INTERVAL + 2.0) * 1e6));
        }
        proc_terminate($process, 15);
        $exitCode = null;
        $this->pollUntil('the daemon exits after SIGTERM', 10.0, 0.05, static function () use ($process, &$exitCode): bool {
            $status = proc_get_status($process);
            $exitCode = $status['running'] ? null : $status['exitcode'];

            return !$status['running'];
        });
        proc_close($process);
        $this->daemon = null;
        if ($catchesSignals) {
            self::assertSame(0, $exitCode, "the daemon did not exit 0 on SIGTERM\n" . $this->daemonOutput());
        }
    }

    private function daemonOutput(): string
    {
        $out = '';
        foreach (['daemon.out', 'daemon.err'] as $name) {
            $text = @file_get_contents($this->dir . '/' . $name);
            $out .= $text === false ? '' : $text;
        }

        return $out;
    }

    /**
     * call calls the management API and returns the status and the JSON body.
     *
     * @param array<string, mixed>|null $body
     * @return array{int, array<array-key, mixed>}
     */
    private function call(string $method, string $path, ?array $body = null): array
    {
        $curl = curl_init($this->base . $path);
        $headers = ['Authorization: Bearer ' . $this->token, 'Accept: application/json'];
        $options = [
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => self::REQUEST_TIMEOUT,
        ];
        if ($body !== null) {
            $headers[] = 'Content-Type: application/json';
            $options[CURLOPT_POSTFIELDS] = json_encode($body, JSON_THROW_ON_ERROR);
        }
        $options[CURLOPT_HTTPHEADER] = $headers;
        curl_setopt_array($curl, $options);
        $text = curl_exec($curl);
        if (!is_string($text)) {
            throw new RuntimeException($method . ' ' . $path . ': ' . curl_error($curl));
        }
        $status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
        $decoded = $text === '' ? [] : json_decode($text, true);

        return [$status, is_array($decoded) ? $decoded : ['body' => $text]];
    }

    /**
     * must calls the management API and fails unless the status is $want.
     *
     * @param array<string, mixed>|null $body
     * @return array<array-key, mixed>
     */
    private function must(int $want, string $method, string $path, ?array $body = null): array
    {
        [$status, $out] = $this->call($method, $path, $body);
        self::assertSame($want, $status, $method . ' ' . $path . ': ' . json_encode($out));

        return $out;
    }

    /** pollUntil checks $condition every $interval seconds until it holds or $timeout passes. */
    private function pollUntil(string $what, float $timeout, float $interval, \Closure $condition): void
    {
        $deadline = microtime(true) + $timeout;
        while (!$condition()) {
            if (microtime(true) > $deadline) {
                self::fail($what . ': not within ' . $timeout . " s\n" . $this->daemonOutput());
            }
            usleep((int) ($interval * 1e6));
        }
    }

    /** closedPort is a local port nothing listens on. */
    private static function closedPort(): int
    {
        $server = stream_socket_server('tcp://127.0.0.1:0');
        if ($server === false) {
            throw new RuntimeException('cannot open a local port');
        }
        $name = (string) stream_socket_get_name($server, false);
        fclose($server);

        return (int) substr($name, strrpos($name, ':') + 1);
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
}
