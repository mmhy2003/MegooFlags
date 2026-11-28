<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Daemon;

use CurlHandle;
use CurlMultiHandle;
use MegooFlags\Config;
use MegooFlags\Internal\Cache\CacheFiles;
use MegooFlags\Internal\Cache\State;
use MegooFlags\Internal\Engine\Compiler;
use MegooFlags\Internal\Engine\ParseException;
use MegooFlags\Internal\Events\Sender;
use MegooFlags\Internal\Events\Spool;
use MegooFlags\Internal\Transport\Backoff;
use MegooFlags\Internal\Transport\Http;
use MegooFlags\Internal\Transport\Response;
use RuntimeException;

/**
 * Daemon is the optional sync process, bin/megooflags-sync. It keeps the
 * cache files of one SDK key fresh so that requests never do upkeep:
 *
 * - it follows GET /sdk/v1/stream (SSE) and writes both cache files and
 *   the state etag on every valid put; a line-less stream is dead after
 *   45 s; reconnects back off from 3 s to 60 s (the server's retry field
 *   replaces the base, Retry-After is honoured, a connection that lasted
 *   60 s resets the backoff);
 * - after two stream failures in a row it polls GET /sdk/v1/ruleset with
 *   If-None-Match every pollInterval, until the stream connects again;
 *   with disableStream it only polls;
 * - every 5 s it writes the state with checked_at = now, so requests skip
 *   upkeep, and every flushInterval it ships the events spool, sending an
 *   exposure once per context key, flag, variant and UTC hour (an LRU of
 *   10 000 keys kept across shipments);
 * - 401, 403 or a revoked event writes the fatal marker and exits 78, as
 *   does an invalid configuration or a fatal marker already present;
 * - SIGTERM and SIGINT (with pcntl) ship the spool once more and exit 0.
 *
 * Everything runs in one curl_multi loop that waits at most 1 s. The key
 * is only sent in the Authorization header and never logged.
 *
 * @internal
 *
 * @phpstan-type Tuning array{dead_timeout: float, stable_after: float, retry_base: float, retry_max: float,
 *     heartbeat: float, poll_interval: ?float, flush_interval: ?float}
 */
final class Daemon
{
    public const EXIT_OK = 0;
    public const EXIT_FAILED = 1;
    public const EXIT_CONFIG = 78;

    /** The defaults; the MEGOOFLAGS_INTERNAL_* variables replace them in tests only. */
    public const TUNING = [
        'dead_timeout' => 45.0,
        'stable_after' => 60.0,
        'retry_base' => 3.0,
        'retry_max' => 60.0,
        'heartbeat' => 5.0,
        'poll_interval' => null,
        'flush_interval' => null,
    ];

    public const USAGE = <<<'TXT'
        Usage: megooflags-sync [options]

        Keeps the MegooFlags cache files of one server SDK key fresh for PHP
        requests: follows the SSE stream (polling when it is down), refreshes
        the state file so requests skip upkeep, and ships the events spool.

        The SDK key is read from the environment only: MEGOOFLAGS_SDK_KEY.

        Options (each also read from the environment variable in brackets;
        a flag wins over the variable):
          --base-url=URL         MegooFlags URL [MEGOOFLAGS_BASE_URL]
          --cache-dir=DIR        the providers' cacheDir [MEGOOFLAGS_CACHE_DIR]
          --poll-interval=SEC    polling interval when the stream is down, at
                                 least 15 (default 30) [MEGOOFLAGS_POLL_INTERVAL]
          --flush-interval=SEC   events shipping interval, 10 to 60 (default 30)
                                 [MEGOOFLAGS_FLUSH_INTERVAL]
          --disable-stream       poll only, no SSE [MEGOOFLAGS_DISABLE_STREAM=1]
          --once                 fetch the ruleset and ship the spool once, then exit
          --help                 print this help and exit

        Exit status: 0 when stopped by SIGTERM or SIGINT or after --once,
        1 when --once failed, 78 for an invalid configuration or an SDK key
        the server refused (401, 403, revoked).

        TXT;

    private const POLL_TIMEOUT = 10.0;
    private const CONNECT_TIMEOUT = 10.0;
    private const SELECT_MAX = 1.0;
    private const EVENTS_BACKOFF_BASE = 2.0;
    private const EVENTS_BACKOFF_MAX = 60.0;
    private const LRU_SIZE = 10_000;
    private const VALUE_OPTIONS = ['base-url', 'cache-dir', 'poll-interval', 'flush-interval'];
    private const FLAG_OPTIONS = ['disable-stream', 'once', 'help'];

    private readonly CacheFiles $files;
    private readonly Http $http;
    private readonly Lru $lru;
    private readonly float $pollInterval;
    private readonly float $flushInterval;

    private readonly CurlMultiHandle $multi;

    private ?CurlHandle $stream = null;
    private int $streamStatus = 0;
    /** @var array<string, string> */
    private array $streamHeaders = [];
    private ?float $connectedAt = null;
    private float $lastLine = 0.0;
    private float $nextStream = 0.0;
    private int $streamFailures = 0;
    private float $retryBase;
    private ?SseParser $parser = null;

    private bool $polling = false;
    private ?CurlHandle $poll = null;
    private int $pollStatus = 0;
    /** @var array<string, string> */
    private array $pollHeaders = [];
    private float $pollStarted = 0.0;
    private float $nextPoll = 0.0;

    private float $nextHeartbeat = 0.0;
    private float $nextFlush = 0.0;
    private int $eventFailures = 0;

    private string $etag = '';
    private ?string $fatal = null;
    private bool $stopping = false;
    /** @var array<string, true> */
    private array $warned = [];

    /**
     * @param Tuning $tuning
     * @param resource $err where log lines go
     */
    public function __construct(
        private readonly Config $config,
        private readonly array $tuning,
        private $err,
    ) {
        $this->files = new CacheFiles($config->cacheDir, $config->sdkKey);
        $this->http = new Http($config->baseUrl, $config->sdkKey);
        $this->lru = new Lru(self::LRU_SIZE);
        $this->pollInterval = $tuning['poll_interval'] ?? $config->pollInterval;
        $this->flushInterval = $tuning['flush_interval'] ?? $config->flushInterval;
        $this->retryBase = $tuning['retry_base'];
        $this->multi = curl_multi_init();
    }

    /**
     * main is bin/megooflags-sync: it reads the flags and the environment,
     * then runs the daemon, and returns the exit status.
     *
     * @param list<string> $argv
     * @param array<string, string> $env
     * @param resource $out
     * @param resource $err
     */
    public static function main(array $argv, array $env, $out, $err): int
    {
        $options = [];
        $args = array_slice($argv, 1);
        for ($i = 0; $i < count($args); $i++) {
            $arg = $args[$i];
            $eq = strpos($arg, '=');
            $name = $eq === false ? $arg : substr($arg, 0, $eq);
            $value = $eq === false ? null : substr($arg, $eq + 1);
            $name = str_starts_with($name, '--') ? substr($name, 2) : '';
            if (in_array($name, self::FLAG_OPTIONS, true) && $value === null) {
                $options[$name] = true;
            } elseif (in_array($name, self::VALUE_OPTIONS, true)) {
                $value ??= $args[++$i] ?? null;
                if ($value === null) {
                    return self::usageError($err, 'option --' . $name . ' needs a value');
                }
                $options[$name] = $value;
            } else {
                return self::usageError($err, 'unknown option ' . self::printable($arg));
            }
        }
        if (isset($options['help'])) {
            fwrite($out, self::USAGE);

            return self::EXIT_OK;
        }

        $setting = static fn(string $option, string $variable): string => (string) ($options[$option] ?? $env[$variable] ?? '');
        $durations = [];
        foreach (['poll-interval' => 'MEGOOFLAGS_POLL_INTERVAL', 'flush-interval' => 'MEGOOFLAGS_FLUSH_INTERVAL'] as $option => $variable) {
            $text = $setting($option, $variable);
            if ($text !== '' && !is_numeric($text)) {
                return self::usageError($err, '--' . $option . ' must be a number of seconds');
            }
            $durations[$option] = $text === '' ? 0.0 : (float) $text;
        }
        $sdkKey = $env['MEGOOFLAGS_SDK_KEY'] ?? '';
        if ($sdkKey === '') {
            return self::usageError($err, 'MEGOOFLAGS_SDK_KEY is not set');
        }
        $disableStream = isset($options['disable-stream'])
            || in_array(strtolower($env['MEGOOFLAGS_DISABLE_STREAM'] ?? ''), ['1', 'true', 'yes', 'on'], true);
        $config = new Config(
            sdkKey: $sdkKey,
            baseUrl: $setting('base-url', 'MEGOOFLAGS_BASE_URL'),
            cacheDir: $setting('cache-dir', 'MEGOOFLAGS_CACHE_DIR'),
            disableStream: $disableStream,
            pollInterval: $durations['poll-interval'],
            flushInterval: $durations['flush-interval'],
        );

        return (new self($config, self::tuning($env), $err))->run(isset($options['once']));
    }

    /**
     * tuning reads the MEGOOFLAGS_INTERNAL_* variables (positive seconds)
     * that the daemon tests use to shorten the timings.
     *
     * @param array<string, string> $env
     * @return Tuning
     */
    public static function tuning(array $env): array
    {
        $tuning = self::TUNING;
        foreach (array_keys($tuning) as $name) {
            $value = $env['MEGOOFLAGS_INTERNAL_' . strtoupper($name)] ?? '';
            if (is_numeric($value) && (float) $value > 0) {
                $tuning[$name] = (float) $value;
            }
        }

        return $tuning;
    }

    /** run syncs until stopped (or once) and returns the exit status. */
    public function run(bool $once = false): int
    {
        $error = $this->config->error();
        if ($error !== null) {
            $this->log('error', $error);

            return self::EXIT_CONFIG;
        }
        $state = State::load($this->files->statePath());
        if ($state->isFatal()) {
            $this->log('error', Response::fatalMessage((string) $state->fatal) . ' before; the fatal marker in '
                . $this->files->statePath() . ' stops every sync with this key');

            return self::EXIT_CONFIG;
        }
        $this->etag = $this->files->loadJson()['etag'] ?? '';

        return $once ? $this->once() : $this->loop();
    }

    private function once(): int
    {
        $now = microtime(true);
        $headers = $this->etag !== '' ? ['If-None-Match' => $this->etag] : [];
        $fetched = $this->pollDone($this->http->get('/sdk/v1/ruleset', self::POLL_TIMEOUT, $headers), $now);
        $retryAfter = $this->fatal === null ? $this->ship() : null;
        if ($this->fatal !== null) {
            return $this->fatalExit();
        }
        $this->updateState(function (State $state) use ($now, $fetched, $retryAfter): void {
            $state->checkedAt = $now;
            if (!$fetched || $retryAfter !== null) {
                $state->failures++;
                $delay = Backoff::delay($state->failures, self::EVENTS_BACKOFF_BASE, self::EVENTS_BACKOFF_MAX, self::rand());
                $state->nextAttempt = $now + max($retryAfter ?? 0.0, $delay);
            }
        });

        return $fetched && $retryAfter === null ? self::EXIT_OK : self::EXIT_FAILED;
    }

    private function loop(): int
    {
        $this->installSignals();
        $this->polling = $this->config->disableStream;
        $now = microtime(true);
        $this->heartbeat($now);
        $this->nextFlush = $now + $this->flushInterval;
        $this->log('info', 'started for SDK key ' . CacheFiles::hash($this->config->sdkKey) . ' (hash) in ' . $this->files->dir());

        while (!$this->stopping && $this->fatal === null) {
            $now = microtime(true);
            if (!$this->config->disableStream && $this->stream === null && $now >= $this->nextStream) {
                $this->openStream($now);
            }
            if ($this->polling && $this->poll === null && $now >= $this->nextPoll) {
                $this->openPoll($now);
            }
            $this->exec();
            if ($this->fatal !== null) {
                break;
            }
            $now = microtime(true);
            if ($this->stream !== null && $now - $this->lastLine >= $this->tuning['dead_timeout']) {
                $this->closeStream();
                $this->streamEnded('dead connection, nothing received for ' . $this->tuning['dead_timeout'] . 's', null);
            }
            if ($now >= $this->nextHeartbeat) {
                $this->heartbeat($now);
            }
            if ($this->fatal === null && !$this->stopping && $now >= $this->nextFlush) {
                $this->flush($now);
            }
            if ($this->fatal === null && !$this->stopping) {
                $this->wait($now);
            }
        }

        $this->closeStream();
        $this->closePoll();
        if ($this->fatal !== null) {
            return $this->fatalExit();
        }
        $this->ship();
        if ($this->fatal !== null) {
            return $this->fatalExit();
        }
        $this->log('info', 'stopped');

        return self::EXIT_OK;
    }

    private function installSignals(): void
    {
        if (!function_exists('pcntl_async_signals')) {
            return;
        }
        pcntl_async_signals(true);
        $stop = function (): void {
            $this->stopping = true;
        };
        pcntl_signal(SIGTERM, $stop);
        pcntl_signal(SIGINT, $stop);
    }

    private function exec(): void
    {
        do {
            $status = curl_multi_exec($this->multi, $running);
        } while ($status === CURLM_CALL_MULTI_PERFORM);
        while (($info = curl_multi_info_read($this->multi)) !== false) {
            $handle = $info['handle'];
            if ($handle === $this->stream) {
                $this->streamDone($info['result']);
            } elseif ($handle === $this->poll) {
                $this->pollFinished($info['result']);
            }
            if ($this->fatal !== null) {
                return;
            }
        }
    }

    /** wait sleeps until the next timer, at most 1 s, waking up for network activity. */
    private function wait(float $now): void
    {
        $next = min($this->nextHeartbeat, $this->nextFlush);
        if ($this->stream !== null) {
            $next = min($next, $this->lastLine + $this->tuning['dead_timeout']);
        } elseif (!$this->config->disableStream) {
            $next = min($next, $this->nextStream);
        }
        if ($this->polling && $this->poll === null) {
            $next = min($next, $this->nextPoll);
        }
        $timeout = max(0.005, min(self::SELECT_MAX, $next - $now));
        if ($this->stream === null && $this->poll === null) {
            usleep((int) ($timeout * 1_000_000));

            return;
        }
        if (curl_multi_select($this->multi, $timeout) === -1) {
            usleep((int) ($timeout * 1_000_000));
        }
    }

    private function openStream(float $now): void
    {
        $extra = ['Accept' => 'text/event-stream', 'Cache-Control' => 'no-cache'];
        if ($this->etag !== '') {
            $extra['Last-Event-ID'] = $this->etag;
        }
        $this->streamStatus = 0;
        $this->streamHeaders = [];
        $this->connectedAt = null;
        $this->lastLine = $now;
        $this->parser = new SseParser(
            function (): void {
                $this->lastLine = microtime(true);
            },
            function (int $ms): void {
                $this->retryBase = $ms / 1000;
            },
            $this->onEvent(...),
        );
        $handle = curl_init();
        curl_setopt_array($handle, $this->options('/sdk/v1/stream', $extra) + [
            CURLOPT_HEADERFUNCTION => function (CurlHandle $h, string $line): int {
                $this->collectHeader($line, $this->streamStatus, $this->streamHeaders);
                if (trim($line) === '' && $this->streamStatus === 200 && $this->connectedAt === null) {
                    $this->connected();
                }

                return strlen($line);
            },
            CURLOPT_WRITEFUNCTION => function (CurlHandle $h, string $chunk): int {
                if ($this->streamStatus !== 200 || $this->parser === null) {
                    return strlen($chunk);
                }
                $this->parser->feed($chunk);

                // A different length aborts the transfer after revoked.
                return $this->fatal === null ? strlen($chunk) : 0;
            },
        ]);
        curl_multi_add_handle($this->multi, $handle);
        $this->stream = $handle;
    }

    private function connected(): void
    {
        $now = microtime(true);
        $this->connectedAt = $now;
        $this->lastLine = $now;
        if ($this->polling && !$this->config->disableStream) {
            $this->polling = false;
            $this->log('info', 'stream connected; polling stopped');
        }
    }

    private function onEvent(string $name, string $data, string $id): void
    {
        if ($this->fatal !== null) {
            return;
        }
        if ($name === 'put' && !$this->apply($data, $id)) {
            $this->log('warning', 'stream sent an invalid ruleset (event id ' . self::printable($id) . '); keeping the previous one');
        } elseif ($name === 'revoked') {
            $this->fatal = 'revoked';
        }
    }

    private function streamDone(int $result): void
    {
        $status = $this->streamStatus;
        $headers = $this->streamHeaders;
        $this->closeStream();
        if ($this->fatal !== null) {
            return;
        }
        $response = new Response($status, $headers, '', $result !== CURLE_OK ? curl_strerror($result) : 'stream ended');
        $fatal = $response->fatal();
        if ($fatal !== null) {
            $this->fatal = $fatal;

            return;
        }
        $reason = $status === 200 ? 'stream ended' : (string) $response->errorMessage();
        $this->streamEnded($reason, $status === 429 ? Http::parseRetryAfter($headers['retry-after'] ?? null, microtime(true)) ?? 0.0 : null);
    }

    /**
     * streamEnded schedules the reconnect with backoff and starts polling
     * after the second failure in a row.
     */
    private function streamEnded(string $reason, ?float $retryAfter): void
    {
        $now = microtime(true);
        if ($this->connectedAt !== null && $now - $this->connectedAt >= $this->tuning['stable_after']) {
            $this->streamFailures = 0;
        }
        $this->connectedAt = null;
        $this->streamFailures++;
        $wait = Backoff::delay($this->streamFailures, $this->retryBase, $this->tuning['retry_max'], self::rand());
        if ($retryAfter !== null) {
            $wait = max($retryAfter, $wait);
        }
        $this->nextStream = $now + $wait;
        $this->log('info', sprintf('stream disconnected (%s), failures %d, retry in %.3fs', $reason, $this->streamFailures, $wait));
        if ($this->streamFailures >= 2 && !$this->polling) {
            $this->polling = true;
            $this->log('warning', 'stream unavailable; polling every ' . $this->pollInterval . 's until it reconnects');
        }
    }

    private function closeStream(): void
    {
        if ($this->stream !== null) {
            curl_multi_remove_handle($this->multi, $this->stream);
            curl_close($this->stream);
            $this->stream = null;
        }
        $this->parser = null;
    }

    private function openPoll(float $now): void
    {
        $extra = $this->etag !== '' ? ['If-None-Match' => $this->etag] : [];
        $this->pollStatus = 0;
        $this->pollHeaders = [];
        $this->pollStarted = $now;
        $handle = curl_init();
        curl_setopt_array($handle, $this->options('/sdk/v1/ruleset', $extra) + [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT_MS => (int) (self::POLL_TIMEOUT * 1000),
            CURLOPT_HEADERFUNCTION => function (CurlHandle $h, string $line): int {
                $this->collectHeader($line, $this->pollStatus, $this->pollHeaders);

                return strlen($line);
            },
        ]);
        curl_multi_add_handle($this->multi, $handle);
        $this->poll = $handle;
    }

    private function pollFinished(int $result): void
    {
        assert($this->poll !== null);
        $body = (string) curl_multi_getcontent($this->poll);
        $status = $this->pollStatus;
        $headers = $this->pollHeaders;
        $this->closePoll();
        $response = $result !== CURLE_OK || $status === 0
            ? new Response(0, [], '', curl_strerror($result) ?? 'no response')
            : new Response($status, $headers, $body, null, Http::parseRetryAfter($headers['retry-after'] ?? null, microtime(true)));
        $this->pollDone($response, $this->pollStarted);
    }

    /**
     * pollDone applies a GET /sdk/v1/ruleset response and schedules the
     * next poll at least pollInterval after $started; true on success.
     */
    private function pollDone(Response $response, float $started): bool
    {
        $fatal = $response->fatal();
        if ($fatal !== null) {
            $this->fatal = $fatal;

            return false;
        }
        $this->nextPoll = $started + max($response->retryAfterSeconds() ?? 0.0, $this->pollInterval);
        if ($response->isNotModified()) {
            return true;
        }
        if ($response->status === 200) {
            if ($this->apply($response->body, $response->header('ETag') ?? '')) {
                return true;
            }
            $this->log('warning', 'the server sent an invalid ruleset; keeping the previous one');

            return false;
        }
        $this->log('warning', 'poll failed: ' . $response->errorMessage());

        return false;
    }

    private function closePoll(): void
    {
        if ($this->poll === null) {
            return;
        }
        curl_multi_remove_handle($this->multi, $this->poll);
        curl_close($this->poll);
        $this->poll = null;
    }

    /**
     * apply validates a ruleset and writes both cache files, then the state
     * etag; false when the ruleset is invalid. A write error is only logged.
     */
    private function apply(string $raw, string $etag): bool
    {
        try {
            $compiled = Compiler::compileJson($raw);
        } catch (ParseException $e) {
            $this->log('warning', 'invalid ruleset: ' . $e->getMessage());

            return false;
        }
        foreach ($compiled['warnings'] as $warning) {
            if (!isset($this->warned[$warning])) {
                $this->warned[$warning] = true;
                $this->log('warning', 'ruleset: ' . $warning);
            }
        }
        try {
            $this->files->saveNetwork($raw, $etag, $compiled);
        } catch (RuntimeException $e) {
            $this->log('warning', 'cannot write the cache: ' . $e->getMessage());

            return true;
        }
        $this->etag = $etag;
        $this->updateState(static function (State $state) use ($etag): void {
            $state->etag = $etag;
            $state->failures = 0;
            $state->nextAttempt = 0.0;
            $state->checkedAt = microtime(true);
        });

        return true;
    }

    /**
     * heartbeat writes checked_at = now so that requests skip upkeep. A
     * fatal marker written by a request stops the daemon.
     */
    private function heartbeat(float $now): void
    {
        $this->nextHeartbeat = $now + $this->tuning['heartbeat'];
        $this->updateState(static function (State $state) use ($now): void {
            $state->checkedAt = $now;
        });
    }

    private function flush(float $now): void
    {
        $retryAfter = $this->ship();
        if ($retryAfter === null) {
            $this->eventFailures = 0;
            $this->nextFlush = $now + $this->flushInterval;

            return;
        }
        $this->eventFailures++;
        $delay = Backoff::delay($this->eventFailures, self::EVENTS_BACKOFF_BASE, self::EVENTS_BACKOFF_MAX, self::rand());
        $this->nextFlush = $now + max($retryAfter, $delay);
    }

    /**
     * ship claims the spool and sends it with the LRU dedup. The unsent
     * remainder goes back to the spool; the result is the wait for a retry,
     * else null. It sets fatal when the server refused the key.
     *
     * @phpstan-impure
     */
    private function ship(): ?float
    {
        $spool = new Spool($this->files->spoolPath());
        $claimed = $spool->claim();
        if ($claimed === []) {
            return null;
        }
        $result = (new Sender($this->http, $this->lru->seen(...)))->ship($spool->lines($claimed));
        if ($result['fatal'] !== null) {
            $this->fatal = $result['fatal'];
        } elseif ($result['remainder'] !== null && !$spool->append($result['remainder'])) {
            $this->log('warning', 'the events spool is full; unsent events were dropped');
        }
        $spool->delete($claimed);
        if ($result['dropped'] > 0) {
            $this->log('warning', 'dropped ' . $result['dropped'] . ' event items the server did not accept');
        }
        if ($result['retryAfter'] !== null) {
            $this->log('warning', 'events not sent; retrying later');
        }

        return $result['retryAfter'];
    }

    /**
     * updateState applies $change to the state file. When the file holds a
     * fatal marker, the daemon stops instead.
     *
     * @param \Closure(State): void $change
     */
    private function updateState(\Closure $change): void
    {
        $path = $this->files->statePath();
        $state = State::load($path);
        if ($state->isFatal()) {
            $this->fatal ??= $state->fatal;

            return;
        }
        $change($state);
        try {
            $state->save($path);
        } catch (RuntimeException $e) {
            $this->log('warning', 'cannot write the state file: ' . $e->getMessage());
        }
    }

    private function fatalExit(): int
    {
        $reason = (string) $this->fatal;
        $path = $this->files->statePath();
        $state = State::load($path);
        $state->fatal = $reason;
        try {
            $state->save($path);
        } catch (RuntimeException $e) {
            $this->log('warning', 'cannot write the fatal marker: ' . $e->getMessage());
        }
        $this->log('error', Response::fatalMessage($reason) . '; the cached ruleset keeps being served');

        return self::EXIT_CONFIG;
    }

    /**
     * @param non-empty-string $path
     * @param array<string, string> $extra
     * @return array<int, mixed>
     */
    private function options(string $path, array $extra): array
    {
        return [
            CURLOPT_URL => $this->http->url($path),
            CURLOPT_HTTPGET => true,
            CURLOPT_HTTPHEADER => $this->http->headerLines($extra),
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_NOSIGNAL => true,
            CURLOPT_CONNECTTIMEOUT_MS => (int) (self::CONNECT_TIMEOUT * 1000),
            CURLOPT_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS,
        ];
    }

    /**
     * @param array<string, string> $headers
     */
    private function collectHeader(string $line, int &$status, array &$headers): void
    {
        $trimmed = trim($line);
        if (str_starts_with($trimmed, 'HTTP/')) {
            $parts = explode(' ', $trimmed, 3);
            $status = (int) ($parts[1] ?? 0);
            $headers = [];
        } elseif (str_contains($trimmed, ':')) {
            [$name, $value] = explode(':', $trimmed, 2);
            $headers[strtolower(trim($name))] = trim($value);
        }
    }

    private function log(string $level, string $message): void
    {
        $message = str_starts_with($message, 'megooflags: ') ? substr($message, strlen('megooflags: ')) : $message;
        fwrite($this->err, gmdate('Y-m-d\TH:i:s\Z') . ' megooflags-sync ' . $level . ': ' . $message . "\n");
    }

    /** @param resource $err */
    private static function usageError($err, string $message): int
    {
        fwrite($err, 'megooflags-sync: ' . $message . "\nRun megooflags-sync --help for the options.\n");

        return self::EXIT_CONFIG;
    }

    private static function printable(string $text): string
    {
        return (string) json_encode($text, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE);
    }

    private static function rand(): float
    {
        return mt_rand() / mt_getrandmax();
    }
}
