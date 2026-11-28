<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Transport;

/**
 * Response is one HTTP exchange. status is 0 and error is set when no
 * response arrived (connection failure, timeout). Header names are
 * lowercase. retryAfter is the parsed Retry-After header in seconds, or
 * null when missing or invalid.
 *
 * The status mapping: 2xx and 304 are no error; 401 and 403 are fatal (the
 * key is refused); 429 is a retry after retryAfterSeconds(); any other
 * status is an HTTP error.
 *
 * @internal
 */
final readonly class Response
{
    /**
     * @param array<string, string> $headers
     */
    public function __construct(
        public int $status,
        public array $headers = [],
        public string $body = '',
        public ?string $error = null,
        public ?float $retryAfter = null,
    ) {}

    public static function fatalMessage(string $reason): string
    {
        return 'megooflags: SDK key refused (' . $reason . ')';
    }

    public function header(string $name): ?string
    {
        return $this->headers[strtolower($name)] ?? null;
    }

    public function isSuccess(): bool
    {
        return $this->status >= 200 && $this->status < 300;
    }

    public function isNotModified(): bool
    {
        return $this->status === 304;
    }

    /** fatal is '401' or '403' when the server refused the key, else null. */
    public function fatal(): ?string
    {
        return $this->status === 401 || $this->status === 403 ? (string) $this->status : null;
    }

    /** retryAfterSeconds is the wait a 429 asks for (0 without Retry-After), else null. */
    public function retryAfterSeconds(): ?float
    {
        return $this->status === 429 ? ($this->retryAfter ?? 0.0) : null;
    }

    /** errorMessage describes the failure, or is null for 2xx and 304. */
    public function errorMessage(): ?string
    {
        if ($this->status === 0) {
            return 'megooflags: network error: ' . ($this->error ?? 'no response');
        }
        if ($this->isSuccess() || $this->isNotModified()) {
            return null;
        }
        $fatal = $this->fatal();
        if ($fatal !== null) {
            return self::fatalMessage($fatal);
        }
        $after = $this->retryAfterSeconds();
        if ($after !== null) {
            return 'megooflags: rate limited, retry after ' . self::seconds($after) . 's';
        }

        return 'megooflags: HTTP ' . $this->status;
    }

    private static function seconds(float $value): string
    {
        return $value === floor($value) ? (string) (int) $value : (string) $value;
    }
}
