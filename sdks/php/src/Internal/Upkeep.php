<?php

declare(strict_types=1);

namespace MegooFlags\Internal;

use MegooFlags\Internal\Cache\CacheFiles;
use MegooFlags\Internal\Cache\Lock;
use MegooFlags\Internal\Cache\State;
use MegooFlags\Internal\Engine\Compiler;
use MegooFlags\Internal\Events\Sender;
use MegooFlags\Internal\Events\Spool;
use MegooFlags\Internal\Transport\Backoff;
use MegooFlags\Internal\Transport\Http;
use RuntimeException;

/**
 * Upkeep is the work a request does at its end when nobody else did it
 * recently: refresh the cached ruleset with a conditional GET and ship the
 * events spool. It runs at most once per pollInterval across all
 * processes (the state file's checked_at), never before next_attempt, never
 * after a fatal marker, and only while holding the lock.
 *
 * Failures (429, 5xx, network errors, an invalid ruleset, events that
 * could not be sent) raise the failure count and set next_attempt to
 * now + max(Retry-After, backoff) with base 2 s and max 60 s. A 401 or
 * 403 writes the fatal marker, after which upkeep does nothing.
 *
 * @internal
 */
final class Upkeep
{
    private const TIMEOUT = 10.0;

    private const BACKOFF_BASE = 2.0;

    private const BACKOFF_MAX = 60.0;

    private readonly Lock $lock;

    private readonly \Closure $rand;

    /**
     * @param (\Closure(): float)|null $rand jitter source in [0, 1]
     */
    public function __construct(
        private readonly CacheFiles $files,
        private readonly Http $http,
        private readonly float $pollInterval,
        ?\Closure $rand = null,
    ) {
        $this->lock = new Lock($files->lockPath());
        $this->rand = $rand ?? static fn(): float => mt_rand() / mt_getrandmax();
    }

    /**
     * acquireIfDue takes the lock and returns true when upkeep is due: no
     * fatal marker, checked_at at least pollInterval ago and next_attempt
     * passed. The state is checked again once the lock is held, since
     * another process may just have finished.
     */
    public function acquireIfDue(float $now): bool
    {
        if (!$this->due($now) || !$this->lock->tryAcquire()) {
            return false;
        }
        if (!$this->due($now)) {
            $this->lock->release();

            return false;
        }

        return true;
    }

    public function release(): void
    {
        $this->lock->release();
    }

    /**
     * run refreshes the ruleset, ships the spool and writes the state with
     * checked_at = $now, then releases the lock. It takes the lock itself
     * when acquireIfDue was not called, and does nothing when another
     * process holds it or the state has a fatal marker.
     */
    public function run(float $now): void
    {
        if (!$this->lock->tryAcquire()) {
            return;
        }
        try {
            $state = State::load($this->files->statePath());
            if ($state->isFatal()) {
                return;
            }
            $retryAfter = self::later($this->refresh($state), $this->ship($state));
            if ($retryAfter !== null) {
                $state->failures++;
                $delay = Backoff::delay($state->failures, self::BACKOFF_BASE, self::BACKOFF_MAX, ($this->rand)());
                $state->nextAttempt = $now + max($retryAfter, $delay);
            }
            $state->checkedAt = $now;
            try {
                $state->save($this->files->statePath());
            } catch (RuntimeException) {
                // An unwritable cache directory: the next request tries again.
            }
        } finally {
            $this->lock->release();
        }
    }

    /** @phpstan-impure it reads the state file */
    private function due(float $now): bool
    {
        $state = State::load($this->files->statePath());

        return !$state->isFatal()
            && $now - $state->checkedAt >= $this->pollInterval
            && $now >= $state->nextAttempt;
    }

    /**
     * refresh sends the conditional GET and updates $state; it returns the
     * wait the server asked for when the attempt failed, else null.
     */
    private function refresh(State $state): ?float
    {
        $headers = $state->etag !== '' ? ['If-None-Match' => $state->etag] : [];
        $response = $this->http->get('/sdk/v1/ruleset', self::TIMEOUT, $headers);
        $fatal = $response->fatal();
        if ($fatal !== null) {
            $state->fatal = $fatal;

            return null;
        }
        if ($response->isNotModified()) {
            self::succeeded($state);

            return null;
        }
        if ($response->status !== 200) {
            return $response->retryAfterSeconds() ?? 0.0;
        }
        $etag = $response->header('ETag') ?? '';
        try {
            $this->files->saveNetwork($response->body, $etag, Compiler::compileJson($response->body));
        } catch (RuntimeException) {
            // ParseException is a RuntimeException too: an invalid ruleset.
            return 0.0;
        }
        $state->etag = $etag;
        self::succeeded($state);

        return null;
    }

    /**
     * ship claims the spool and sends it, unless the key was refused. An
     * unsent remainder is appended back to the spool; the result is the
     * wait for a retry, else null.
     */
    private function ship(State $state): ?float
    {
        if ($state->isFatal()) {
            return null;
        }
        $spool = new Spool($this->files->spoolPath());
        $claimed = $spool->claim();
        if ($claimed === []) {
            return null;
        }
        $result = (new Sender($this->http))->ship($spool->lines($claimed));
        if ($result['fatal'] !== null) {
            $state->fatal = $result['fatal'];
        } elseif ($result['remainder'] !== null) {
            $spool->append($result['remainder']);
        }
        $spool->delete($claimed);

        return $result['retryAfter'];
    }

    private static function succeeded(State $state): void
    {
        $state->failures = 0;
        $state->nextAttempt = 0.0;
    }

    private static function later(?float $a, ?float $b): ?float
    {
        if ($a === null || $b === null) {
            return $a ?? $b;
        }

        return max($a, $b);
    }
}
