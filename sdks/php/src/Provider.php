<?php

declare(strict_types=1);

namespace MegooFlags;

use MegooFlags\Internal\Cache\CacheFiles;
use MegooFlags\Internal\Cache\Lock;
use MegooFlags\Internal\Cache\State;
use MegooFlags\Internal\Engine\Compiler;
use MegooFlags\Internal\Engine\Evaluator;
use MegooFlags\Internal\Engine\Result;
use MegooFlags\Internal\Events\Recorder;
use MegooFlags\Internal\Events\Spool;
use MegooFlags\Internal\Transport\Backoff;
use MegooFlags\Internal\Transport\Http;
use MegooFlags\Internal\Transport\Response;
use MegooFlags\Internal\Upkeep;
use OpenFeature\implementation\provider\AbstractProvider;
use OpenFeature\implementation\provider\ResolutionDetailsBuilder;
use OpenFeature\implementation\provider\ResolutionError;
use OpenFeature\interfaces\flags\EvaluationContext;
use OpenFeature\interfaces\provider\ErrorCode;
use OpenFeature\interfaces\provider\Reason;
use OpenFeature\interfaces\provider\ResolutionDetails;
use RuntimeException;
use Throwable;
use WeakReference;

/**
 * Provider is the MegooFlags OpenFeature provider for PHP. It evaluates
 * flags locally against the ruleset in the shared cache directory.
 *
 * PHP has no long-lived process, so the provider loads lazily on the first
 * evaluation: the compiled cache file, else the JSON cache, else the
 * bootstrap file, else (unless offline or the key was refused) one
 * synchronous fetch bounded by initTimeout. Evaluations then do no I/O.
 * flush() runs at the end of the request (registered with
 * register_shutdown_function): it appends this request's counts and
 * exposures to the events spool and, when nobody refreshed the cache for
 * pollInterval, does the upkeep (a conditional fetch and shipping the
 * spool) after the response was sent.
 *
 * The PHP OpenFeature SDK has no init, events or PROVIDER_FATAL code: an
 * invalid config, a refused key or no ruleset gives the caller's default
 * with PROVIDER_NOT_READY and a message.
 *
 * @phpstan-import-type Ruleset from Compiler
 */
final class Provider extends AbstractProvider
{
    protected static string $NAME = 'MegooFlags';

    private const LOCK_RETRY_MICROSECONDS = 50_000;

    private const BACKOFF_BASE = 2.0;

    private const BACKOFF_MAX = 60.0;

    private readonly ?string $configError;

    private readonly CacheFiles $files;

    private readonly Evaluator $evaluator;

    private readonly Recorder $recorder;

    private bool $loaded = false;

    /** @var Ruleset|null */
    private ?array $ruleset = null;

    private string $notReady = 'megooflags: no ruleset';

    private bool $fatal = false;

    private bool $shutdownRegistered = false;

    public function __construct(private readonly Config $config)
    {
        $this->setLogger($config->logger);
        $this->configError = $config->error();
        if ($this->configError !== null) {
            $config->logger->error($this->configError);
        }
        $this->files = new CacheFiles($config->cacheDir, $config->sdkKey);
        $this->evaluator = new Evaluator(true);
        $this->recorder = new Recorder($config->sendContext);
    }

    public function resolveBooleanValue(string $flagKey, bool $defaultValue, ?EvaluationContext $context = null): ResolutionDetails
    {
        return $this->resolve($flagKey, $defaultValue, $context, 'boolean');
    }

    public function resolveStringValue(string $flagKey, string $defaultValue, ?EvaluationContext $context = null): ResolutionDetails
    {
        return $this->resolve($flagKey, $defaultValue, $context, 'string');
    }

    public function resolveIntegerValue(string $flagKey, int $defaultValue, ?EvaluationContext $context = null): ResolutionDetails
    {
        return $this->resolve($flagKey, $defaultValue, $context, 'integer');
    }

    public function resolveFloatValue(string $flagKey, float $defaultValue, ?EvaluationContext $context = null): ResolutionDetails
    {
        return $this->resolve($flagKey, $defaultValue, $context, 'float');
    }

    /**
     * @param mixed[] $defaultValue
     */
    public function resolveObjectValue(string $flagKey, array $defaultValue, ?EvaluationContext $context = null): ResolutionDetails
    {
        return $this->resolve($flagKey, $defaultValue, $context, 'object');
    }

    /**
     * flush ends a request: it appends the evaluations recorded since the
     * last flush to the events spool as one line, then does the upkeep when
     * it is due. Long-running workers call it themselves, for example after
     * each job. It does nothing offline, with an invalid config or once the
     * key was refused, and it never throws.
     */
    public function flush(): void
    {
        $this->flushRequest(false);
    }

    private function flushRequest(bool $atShutdown): void
    {
        if ($this->configError !== null || $this->config->offline) {
            return;
        }
        try {
            if (State::load($this->files->statePath())->isFatal()) {
                $this->recorder->take();

                return;
            }
            $line = $this->recorder->take();
            if ($line !== null && !(new Spool($this->files->spoolPath()))->append($line)) {
                $this->logger?->warning('megooflags: events dropped: the spool is full or not writable');
            }
            $upkeep = new Upkeep($this->files, $this->http(), $this->config->pollInterval);
            $now = microtime(true);
            if (!$upkeep->acquireIfDue($now)) {
                return;
            }
            // Only at the end of the request: send the response first.
            if ($atShutdown && function_exists('fastcgi_finish_request')) {
                fastcgi_finish_request();
            }
            $upkeep->run($now);
        } catch (Throwable $e) {
            $this->logger?->warning('megooflags: flush failed: ' . $e->getMessage());
        }
    }

    /**
     * @param 'boolean'|'string'|'integer'|'float'|'object' $type
     * @param bool|string|int|float|mixed[] $default
     */
    private function resolve(string $flagKey, bool|string|int|float|array $default, ?EvaluationContext $context, string $type): ResolutionDetails
    {
        try {
            $this->load();
            if ($this->ruleset === null) {
                return self::error($default, ErrorCode::PROVIDER_NOT_READY(), $this->configError ?? $this->notReady);
            }
            $attrs = $context?->getAttributes()->toArray() ?? [];
            $targetingKey = $context?->getTargetingKey();
            if ($targetingKey !== null && $targetingKey !== '') {
                $attrs['key'] = $targetingKey;
            }
            $expected = match ($type) {
                'integer', 'float' => 'number',
                'object' => 'json',
                default => $type,
            };
            $result = $this->evaluator->evaluate($this->ruleset, $flagKey, (object) $attrs, $default, $expected);
            $details = $this->details($flagKey, $result, $default, $type);
            $this->record($flagKey, $result, $details->getError() === null, $attrs);

            return $details;
        } catch (Throwable $e) {
            return self::error($default, ErrorCode::GENERAL(), 'megooflags: ' . $e->getMessage());
        }
    }

    /**
     * details maps an engine result to the OpenFeature result, converting a
     * served value to the PHP type the client checks.
     *
     * @param bool|string|int|float|mixed[] $default
     */
    private function details(string $flagKey, Result $result, bool|string|int|float|array $default, string $type): ResolutionDetails
    {
        if ($result->errorCode !== null) {
            return self::error($default, self::errorCode($result->errorCode), (string) $result->errorMessage);
        }
        $value = $result->value;
        if ($result->variant !== null) {
            $value = match ($type) {
                'integer' => self::integer($value),
                'float' => (float) $value,
                'object' => is_array($value) ? $value : null,
                default => $value,
            };
            if ($value === null) {
                return self::error($default, ErrorCode::TYPE_MISMATCH(), "flag '{$flagKey}' is not " . ($type === 'integer' ? 'an integer' : 'an object'));
            }
        } else {
            $value = $default;
        }
        $builder = (new ResolutionDetailsBuilder())
            ->withValue($value)
            ->withReason($result->reason);
        if ($result->variant !== null) {
            $builder->withVariant($result->variant);
        }
        $metadata = $result->metadata();
        if ($metadata !== []) {
            $builder->withMetadata($metadata);
        }

        return $builder->build();
    }

    /**
     * @param array<array-key, mixed> $attrs
     */
    private function record(string $flagKey, Result $result, bool $ok, array $attrs): void
    {
        if ($this->config->offline || $this->fatal) {
            return;
        }
        $contextKey = $attrs['key'] ?? '';
        $this->recorder->record(
            $flagKey,
            $result->variant,
            $ok && $result->variant !== null,
            $result->reason,
            is_string($contextKey) ? $contextKey : '',
            $attrs,
            microtime(true),
        );
    }

    /**
     * load finds the ruleset once per provider, and registers flush() to
     * run at shutdown.
     */
    private function load(): void
    {
        if ($this->loaded) {
            return;
        }
        $this->loaded = true;
        if ($this->configError !== null) {
            return;
        }
        $this->registerShutdown();
        $state = State::load($this->files->statePath());
        $this->fatal = $state->isFatal();
        $this->ruleset = $this->loadCached($state) ?? $this->loadBootstrap();
        if ($this->ruleset !== null) {
            return;
        }
        if ($state->isFatal()) {
            $this->notReady = Response::fatalMessage((string) $state->fatal);
        } elseif ($this->config->offline) {
            $this->notReady = 'megooflags: offline and no ruleset in the cache or the bootstrap file';
        } elseif (microtime(true) < $state->nextAttempt) {
            $this->notReady = 'megooflags: no ruleset; the last fetch failed, next attempt at ' . gmdate('Y-m-d\TH:i:s\Z', (int) $state->nextAttempt);
        } else {
            $this->ruleset = $this->coldStart();
        }
    }

    /**
     * loadCached returns the compiled file (read again past OPcache when the
     * state's etag says it changed), else compiles the JSON cache and writes
     * the compiled file. A compiled file whose etag is not the state's while
     * the JSON cache has the state's etag was overwritten by an older write
     * (a bootstrap or JSON-cache compile racing upkeep or the daemon): the
     * JSON cache is served and compiled again, since the conditional fetch
     * sends the state's etag and would never replace it.
     *
     * $held is the lock when the caller holds it already.
     *
     * @return Ruleset|null
     */
    private function loadCached(State $state, ?Lock $held = null): ?array
    {
        $compiled = $this->files->loadCompiled();
        if ($compiled !== null && $compiled['etag'] !== $state->etag) {
            $compiled = $this->files->loadCompiled(true);
        }
        if ($compiled !== null && $compiled['etag'] === $state->etag && $compiled['source'] === CacheFiles::SOURCE_NETWORK) {
            return $compiled['ruleset'];
        }
        // No compiled file, one older than the state, or the bootstrap one:
        // the JSON cache wins when there is no compiled file or when it is
        // the version the state names (else upkeep is between writing the
        // compiled file and the state, and the compiled file is newer).
        if ($compiled !== null && !is_file($this->files->jsonPath())) {
            return $compiled['ruleset'];
        }
        $json = $this->files->loadJson();
        if ($json === null || ($compiled !== null && $json['etag'] !== $state->etag)) {
            return $compiled['ruleset'] ?? null;
        }
        try {
            $ruleset = Compiler::compile($json['ruleset']);
        } catch (RuntimeException $e) {
            $this->logger?->warning('megooflags: ignoring the cached ruleset: ' . $e->getMessage());

            return $compiled['ruleset'] ?? null;
        }
        $etag = $json['etag'];
        $this->underLock($held, function () use ($etag, $ruleset): void {
            // Write only while the JSON cache still holds this version and
            // the compiled file is not already a network copy of it.
            $current = $this->files->loadCompiled(true);
            if (($this->files->loadJson()['etag'] ?? null) !== $etag
                || ($current !== null && $current['etag'] === $etag && $current['source'] === CacheFiles::SOURCE_NETWORK)) {
                return;
            }
            $this->save(fn() => $this->files->saveCompiledFromJson($etag, $ruleset));
        });

        return $ruleset;
    }

    /**
     * loadBootstrap compiles the bootstrap file. It writes the compiled file
     * only under the lock and only when, looking again under the lock, there
     * is still no cache: another process may have written one since. In that
     * case the cached ruleset is served instead.
     *
     * @return Ruleset|null
     */
    private function loadBootstrap(): ?array
    {
        $path = $this->config->bootstrapFile;
        if ($path === null) {
            return null;
        }
        $text = @file_get_contents($path);
        if ($text === false) {
            $this->logger?->warning('megooflags: cannot read the bootstrap file ' . $path);

            return null;
        }
        try {
            $ruleset = Compiler::compileJson($text);
        } catch (RuntimeException $e) {
            $this->logger?->warning('megooflags: invalid bootstrap file ' . $path . ': ' . $e->getMessage());

            return null;
        }
        $this->underLock(null, function (Lock $lock) use (&$ruleset): void {
            $cached = $this->loadCached(State::load($this->files->statePath()), $lock);
            if ($cached !== null) {
                $ruleset = $cached;

                return;
            }
            $bootstrap = $ruleset;
            $this->save(fn() => $this->files->saveBootstrap($bootstrap));
        });

        return $ruleset;
    }

    /**
     * underLock runs $write holding the lock upkeep and the cold start use,
     * so that a compiled file is never written over one they just wrote. When
     * another process holds the lock, nothing is written: that process is
     * writing the cache, and the ruleset in memory is served anyway.
     *
     * @param \Closure(Lock): void $write
     */
    private function underLock(?Lock $held, \Closure $write): void
    {
        $lock = $held ?? new Lock($this->files->lockPath());
        if (!$lock->tryAcquire()) {
            return;
        }
        try {
            $write($lock);
        } finally {
            if ($held === null) {
                $lock->release();
            }
        }
    }

    /**
     * coldStart fetches the ruleset while holding the lock, bounded by
     * initTimeout overall: another process may be fetching it already, so
     * it waits for the lock and looks at the cache again first.
     *
     * @return Ruleset|null
     */
    private function coldStart(): ?array
    {
        $deadline = microtime(true) + $this->config->initTimeout;
        $lock = new Lock($this->files->lockPath());
        while (!$lock->tryAcquire()) {
            $left = $deadline - microtime(true);
            if ($left <= 0) {
                $this->notReady = 'megooflags: no ruleset within ' . $this->config->initTimeout . 's';

                return null;
            }
            usleep((int) min(self::LOCK_RETRY_MICROSECONDS, ceil($left * 1e6)));
        }
        try {
            $state = State::load($this->files->statePath());
            $cached = $this->loadCached($state, $lock);
            if ($cached !== null) {
                return $cached;
            }

            return $this->fetch($state, max($deadline - microtime(true), 0.001));
        } finally {
            $lock->release();
        }
    }

    /** @return Ruleset|null */
    private function fetch(State $state, float $timeout): ?array
    {
        $response = $this->http()->get('/sdk/v1/ruleset', $timeout);
        $now = microtime(true);
        $fatal = $response->fatal();
        $retryAfter = null;
        $ruleset = null;
        if ($fatal !== null) {
            $state->fatal = $fatal;
            $this->fatal = true;
            $this->notReady = Response::fatalMessage($fatal);
            $this->logger?->error($this->notReady);
        } elseif ($response->status === 200) {
            try {
                $ruleset = Compiler::compileJson($response->body);
                $etag = $response->header('ETag') ?? '';
                $this->save(fn() => $this->files->saveNetwork($response->body, $etag, $ruleset));
                $state->etag = $etag;
                $state->failures = 0;
                $state->nextAttempt = 0.0;
                $state->checkedAt = $now;
            } catch (RuntimeException $e) {
                $this->notReady = 'megooflags: invalid ruleset: ' . $e->getMessage();
                $retryAfter = 0.0;
            }
        } else {
            $this->notReady = $response->errorMessage() ?? ('megooflags: HTTP ' . $response->status);
            $retryAfter = $response->retryAfterSeconds() ?? 0.0;
        }
        if ($retryAfter !== null) {
            $state->failures++;
            $delay = Backoff::delay($state->failures, self::BACKOFF_BASE, self::BACKOFF_MAX, mt_rand() / mt_getrandmax());
            $state->nextAttempt = $now + max($retryAfter, $delay);
            $this->logger?->warning($this->notReady);
        }
        $this->save(fn() => $state->save($this->files->statePath()));

        return $ruleset;
    }

    private function http(): Http
    {
        return new Http($this->config->baseUrl, $this->config->sdkKey);
    }

    /** save runs a cache write; a failure is only logged, the ruleset is still served. */
    private function save(\Closure $write): void
    {
        try {
            $write();
        } catch (RuntimeException $e) {
            $this->logger?->warning($e->getMessage());
        }
    }

    /**
     * registerShutdown runs flush at the end of the request. It holds the
     * provider weakly, so a provider a worker dropped is not kept alive.
     */
    private function registerShutdown(): void
    {
        if ($this->shutdownRegistered) {
            return;
        }
        $this->shutdownRegistered = true;
        $self = WeakReference::create($this);
        register_shutdown_function(static function () use ($self): void {
            $self->get()?->flushRequest(true);
        });
    }

    private static function integer(mixed $value): ?int
    {
        if (is_int($value)) {
            return $value;
        }
        if (is_float($value) && floor($value) === $value && abs($value) < 2 ** 63) {
            return (int) $value;
        }

        return null;
    }

    private static function errorCode(string $code): ErrorCode
    {
        return match ($code) {
            'FLAG_NOT_FOUND' => ErrorCode::FLAG_NOT_FOUND(),
            'TYPE_MISMATCH' => ErrorCode::TYPE_MISMATCH(),
            'INVALID_CONTEXT' => ErrorCode::INVALID_CONTEXT(),
            'PARSE_ERROR' => ErrorCode::PARSE_ERROR(),
            default => ErrorCode::GENERAL(),
        };
    }

    /**
     * @param bool|string|int|float|mixed[] $default
     */
    private static function error(bool|string|int|float|array $default, ErrorCode $code, string $message): ResolutionDetails
    {
        return (new ResolutionDetailsBuilder())
            ->withValue($default)
            ->withReason(Reason::ERROR)
            ->withError(new ResolutionError($code, $message))
            ->build();
    }
}
