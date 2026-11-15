<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Transport;

use DateTimeImmutable;
use DateTimeZone;
use MegooFlags\Version;

/**
 * Http sends the SDK's requests with ext-curl. The SDK key goes in the
 * Authorization header only, never in the URL. A request never throws: a
 * failure to get a response is a Response with status 0.
 *
 * @internal
 */
final class Http
{
    private const HTTP_DATE_FORMATS = [
        'D, d M Y H:i:s \G\M\T', // IMF-fixdate
        'l, d-M-y H:i:s \G\M\T', // RFC 850
        'D M j H:i:s Y',         // asctime
    ];

    private readonly string $baseUrl;

    public function __construct(string $baseUrl, private readonly string $sdkKey)
    {
        $this->baseUrl = rtrim($baseUrl, '/');
    }

    /**
     * @param non-empty-string $path
     * @return non-empty-string
     */
    public function url(string $path): string
    {
        return $this->baseUrl . $path;
    }

    /**
     * headerLines are the request headers: Authorization, User-Agent,
     * Accept, Content-Type when there is a body, then $extra (which can
     * replace Accept).
     *
     * @param array<string, string> $extra
     * @return list<string>
     */
    public function headerLines(array $extra = [], bool $withBody = false): array
    {
        $headers = [
            'Authorization' => 'Bearer ' . $this->sdkKey,
            'User-Agent' => 'megooflags-php/' . Version::VERSION,
            'Accept' => 'application/json',
        ];
        if ($withBody) {
            $headers['Content-Type'] = 'application/json';
            // No "Expect: 100-continue" round trip for large bodies.
            $headers['Expect'] = '';
        }
        $lines = [];
        foreach (array_merge($headers, $extra) as $name => $value) {
            $lines[] = $name . ': ' . $value;
        }

        return $lines;
    }

    /**
     * get sends GET <baseUrl><path>, waiting at most $timeout seconds.
     *
     * @param non-empty-string $path
     * @param array<string, string> $headers extra headers, such as If-None-Match
     */
    public function get(string $path, float $timeout, array $headers = []): Response
    {
        return $this->send('GET', $path, null, $timeout, $headers);
    }

    /**
     * post sends $body as JSON to <baseUrl><path>, waiting at most $timeout seconds.
     *
     * @param non-empty-string $path
     */
    public function post(string $path, string $body, float $timeout): Response
    {
        return $this->send('POST', $path, $body, $timeout, []);
    }

    /**
     * parseRetryAfter reads a Retry-After value in seconds: digits are
     * seconds (a negative number is 0), an HTTP-date is max(date - now, 0),
     * and a missing or invalid value is null.
     */
    public static function parseRetryAfter(?string $value, float $now): ?float
    {
        $value = trim((string) $value);
        if ($value === '') {
            return null;
        }
        if (preg_match('/^[+-]?\d+$/', $value) === 1) {
            return max(0.0, (float) $value);
        }
        $value = (string) preg_replace('/ +/', ' ', $value);
        $utc = new DateTimeZone('UTC');
        foreach (self::HTTP_DATE_FORMATS as $format) {
            $date = DateTimeImmutable::createFromFormat('!' . $format, $value, $utc);
            $errors = DateTimeImmutable::getLastErrors();
            if ($date !== false && ($errors === false || ($errors['warning_count'] === 0 && $errors['error_count'] === 0))) {
                return max(0.0, (float) $date->getTimestamp() - $now);
            }
        }

        return null;
    }

    /**
     * @param 'GET'|'POST' $method
     * @param non-empty-string $path
     * @param array<string, string> $headers
     */
    private function send(string $method, string $path, ?string $body, float $timeout, array $headers): Response
    {
        $handle = curl_init();
        $received = [];
        $timeoutMs = max(1, (int) ceil($timeout * 1000));
        curl_setopt_array($handle, [
            CURLOPT_URL => $this->url($path),
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_HTTPHEADER => $this->headerLines($headers, $body !== null),
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_NOSIGNAL => true,
            CURLOPT_TIMEOUT_MS => $timeoutMs,
            CURLOPT_CONNECTTIMEOUT_MS => $timeoutMs,
            CURLOPT_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS,
            CURLOPT_HEADERFUNCTION => static function ($handle, string $line) use (&$received): int {
                $trimmed = trim($line);
                if (str_starts_with($trimmed, 'HTTP/')) {
                    // A new status line (after 100 Continue): forget earlier headers.
                    $received = [];
                } elseif (str_contains($trimmed, ':')) {
                    [$name, $value] = explode(':', $trimmed, 2);
                    $received[strtolower(trim($name))] = trim($value);
                }

                return strlen($line);
            },
        ]);
        if ($body !== null) {
            curl_setopt($handle, CURLOPT_POSTFIELDS, $body);
        }
        $content = curl_exec($handle);
        $status = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
        $error = curl_errno($handle) !== 0 ? curl_error($handle) : null;
        $now = microtime(true);
        if ($error !== null || !is_string($content) || $status === 0) {
            return new Response(0, [], '', $error ?? 'no response');
        }
        /** @var array<string, string> $received */
        return new Response($status, $received, $content, null, self::parseRetryAfter($received['retry-after'] ?? null, $now));
    }
}
