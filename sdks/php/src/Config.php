<?php

declare(strict_types=1);

namespace MegooFlags;

use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;

/**
 * Config is a Provider's options with the defaults filled in and the
 * intervals clamped. Durations are seconds. Zero, negative, infinite and NaN
 * durations mean the default; the poll interval is at least 15 s and the
 * flush interval 10 to 60 s; the base URL loses its trailing slashes.
 *
 * An invalid config does not throw: error() says why, and the provider
 * returns the caller's defaults with PROVIDER_NOT_READY. Dumps leave the
 * key out.
 */
final readonly class Config
{
    public const DEFAULT_INIT_TIMEOUT = 5.0;
    public const DEFAULT_POLL_INTERVAL = 30.0;
    public const MIN_POLL_INTERVAL = 15.0;
    public const DEFAULT_FLUSH_INTERVAL = 30.0;
    public const MIN_FLUSH_INTERVAL = 10.0;
    public const MAX_FLUSH_INTERVAL = 60.0;

    public string $baseUrl;

    public ?string $bootstrapFile;

    public float $initTimeout;

    public float $pollInterval;

    public float $flushInterval;

    public LoggerInterface $logger;

    /**
     * @param string $sdkKey a server key (mf_server_…)
     * @param string $baseUrl the MegooFlags URL; not needed when offline
     * @param string $cacheDir required: the directory of the shared cache files
     * @param string|null $bootstrapFile a ruleset as GET /sdk/v1/ruleset returns it
     * @param bool $offline no network and no events
     * @param bool $disableStream used by the sync daemon only: poll instead of SSE
     * @param float $flushInterval used by the sync daemon only
     * @param bool $sendContext include the context in exposures
     */
    public function __construct(
        public string $sdkKey,
        string $baseUrl = '',
        public string $cacheDir = '',
        ?string $bootstrapFile = null,
        public bool $offline = false,
        float $initTimeout = self::DEFAULT_INIT_TIMEOUT,
        public bool $disableStream = false,
        float $pollInterval = self::DEFAULT_POLL_INTERVAL,
        float $flushInterval = self::DEFAULT_FLUSH_INTERVAL,
        public bool $sendContext = false,
        ?LoggerInterface $logger = null,
    ) {
        $this->baseUrl = rtrim($baseUrl, '/');
        $this->bootstrapFile = $bootstrapFile === '' ? null : $bootstrapFile;
        $this->initTimeout = self::seconds($initTimeout, self::DEFAULT_INIT_TIMEOUT);
        $this->pollInterval = max(self::seconds($pollInterval, self::DEFAULT_POLL_INTERVAL), self::MIN_POLL_INTERVAL);
        $this->flushInterval = min(
            max(self::seconds($flushInterval, self::DEFAULT_FLUSH_INTERVAL), self::MIN_FLUSH_INTERVAL),
            self::MAX_FLUSH_INTERVAL,
        );
        $this->logger = $logger ?? new NullLogger();
    }

    /** error is why a provider cannot run with this config, or null. It never includes the key. */
    public function error(): ?string
    {
        if (str_starts_with($this->sdkKey, 'mf_client_')) {
            return 'megooflags: a client SDK key cannot be used by a server SDK; use a server key (mf_server_…)';
        }
        if (!str_starts_with($this->sdkKey, 'mf_server_')) {
            return 'megooflags: invalid SDK key; use a server key (mf_server_…)';
        }
        if ($this->cacheDir === '') {
            return 'megooflags: cacheDir is required';
        }
        if (!$this->offline && !self::isHttpUrl($this->baseUrl)) {
            return 'megooflags: baseUrl must be an http or https URL';
        }

        return null;
    }

    /** @return array<string, mixed> */
    public function __debugInfo(): array
    {
        $fields = get_object_vars($this);
        $fields['sdkKey'] = '[hidden]';

        return $fields;
    }

    private static function seconds(float $value, float $default): float
    {
        return is_finite($value) && $value > 0 ? $value : $default;
    }

    private static function isHttpUrl(string $url): bool
    {
        $parts = parse_url($url);
        if ($parts === false) {
            return false;
        }
        $scheme = strtolower($parts['scheme'] ?? '');

        return ($scheme === 'http' || $scheme === 'https') && ($parts['host'] ?? '') !== '';
    }
}
