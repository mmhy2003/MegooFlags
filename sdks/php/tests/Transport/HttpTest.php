<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Transport;

use MegooFlags\Internal\Transport\Http;
use MegooFlags\Tests\Support\FakeServer;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class HttpTest extends TestCase
{
    private const KEY = 'srv-secret-key-123';

    private FakeServer $server;

    protected function setUp(): void
    {
        $this->server = FakeServer::start();
    }

    protected function tearDown(): void
    {
        $this->server->stop();
    }

    private function http(): Http
    {
        return new Http($this->server->url() . '//', self::KEY);
    }

    public function testGetSendsHeadersWithoutBodyHeaders(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 200, 'headers' => ['ETag' => '"v1"'], 'body' => '{"a":1}']);

        $response = $this->http()->get('/sdk/v1/ruleset', 10.0, ['If-None-Match' => '"v0"']);

        self::assertSame(200, $response->status);
        self::assertSame('{"a":1}', $response->body);
        self::assertSame('"v1"', $response->header('ETag'));
        self::assertNull($response->error);
        $requests = $this->server->requests();
        self::assertCount(1, $requests);
        $request = $requests[0];
        self::assertSame('GET', $request['method']);
        self::assertSame('/sdk/v1/ruleset', $request['path']);
        self::assertSame('', $request['query']);
        self::assertSame('Bearer ' . self::KEY, $request['headers']['authorization']);
        self::assertSame('megooflags-php/0.1.0', $request['headers']['user-agent']);
        self::assertSame('application/json', $request['headers']['accept']);
        self::assertSame('"v0"', $request['headers']['if-none-match']);
        self::assertArrayNotHasKey('content-type', $request['headers']);
    }

    public function testPostSendsBodyWithContentType(): void
    {
        $this->server->respond('POST', '/sdk/v1/events', ['status' => 202]);

        $response = $this->http()->post('/sdk/v1/events', '{"counts":[]}', 10.0);

        self::assertSame(202, $response->status);
        self::assertTrue($response->isSuccess());
        $request = $this->server->requests()[0];
        self::assertSame('POST', $request['method']);
        self::assertSame('/sdk/v1/events', $request['path']);
        self::assertSame('{"counts":[]}', $request['body']);
        self::assertSame('application/json', $request['headers']['content-type']);
        self::assertSame('Bearer ' . self::KEY, $request['headers']['authorization']);
        self::assertStringNotContainsString(self::KEY, $request['path'] . '?' . $request['query']);
    }

    public function testChunkedBodyIsReadWhole(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 200, 'body' => 'ab', 'chunks' => [[0.05, 'cd'], [0.05, 'ef']]]);

        self::assertSame('abcdef', $this->http()->get('/sdk/v1/ruleset', 10.0)->body);
    }

    /**
     * @return iterable<string, array{int, array<string, string>, bool, bool, ?string, ?float, ?string}>
     */
    public static function statuses(): iterable
    {
        yield '200' => [200, [], true, false, null, null, null];
        yield '204' => [204, [], true, false, null, null, null];
        yield '304' => [304, [], false, true, null, null, null];
        yield '401' => [401, [], false, false, '401', null, 'megooflags: SDK key refused (401)'];
        yield '403' => [403, [], false, false, '403', null, 'megooflags: SDK key refused (403)'];
        yield '429 with Retry-After' => [429, ['Retry-After' => '5'], false, false, null, 5.0, 'megooflags: rate limited, retry after 5s'];
        yield '429 without Retry-After' => [429, [], false, false, null, 0.0, 'megooflags: rate limited, retry after 0s'];
        yield '500' => [500, [], false, false, null, null, 'megooflags: HTTP 500'];
        yield '404' => [404, [], false, false, null, null, 'megooflags: HTTP 404'];
    }

    /**
     * @param array<string, string> $headers
     */
    #[DataProvider('statuses')]
    public function testStatusMapping(
        int $status,
        array $headers,
        bool $success,
        bool $notModified,
        ?string $fatal,
        ?float $retryAfter,
        ?string $message,
    ): void {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => $status, 'headers' => $headers]);

        $response = $this->http()->get('/sdk/v1/ruleset', 10.0);

        self::assertSame($status, $response->status);
        self::assertSame($success, $response->isSuccess());
        self::assertSame($notModified, $response->isNotModified());
        self::assertSame($fatal, $response->fatal());
        self::assertSame($retryAfter, $response->retryAfterSeconds());
        self::assertSame($message, $response->errorMessage());
    }

    public function testTimeoutIsANetworkError(): void
    {
        $this->server->respond('GET', '/sdk/v1/ruleset', ['status' => 200, 'delay' => 2, 'body' => '{}']);

        $started = microtime(true);
        $response = $this->http()->get('/sdk/v1/ruleset', 0.3);

        self::assertLessThan(1.5, microtime(true) - $started);
        self::assertSame(0, $response->status);
        self::assertNotNull($response->error);
        self::assertFalse($response->isSuccess());
        self::assertNull($response->fatal());
        self::assertNull($response->retryAfterSeconds());
        self::assertStringStartsWith('megooflags: network error: ', (string) $response->errorMessage());
        self::assertStringNotContainsString(self::KEY, (string) $response->errorMessage());
    }

    public function testConnectionRefusedIsANetworkError(): void
    {
        $url = $this->server->url();
        $this->server->stop();

        $response = (new Http($url, self::KEY))->get('/sdk/v1/ruleset', 2.0);

        self::assertSame(0, $response->status);
        self::assertNotNull($response->error);
    }

    public function testUrlTrimsTrailingSlashes(): void
    {
        self::assertSame('http://h:1/base/sdk/v1/ruleset', (new Http('http://h:1/base///', self::KEY))->url('/sdk/v1/ruleset'));
    }

    /**
     * @return iterable<string, array{?string, ?float}>
     */
    public static function retryAfterValues(): iterable
    {
        yield 'seconds' => ['5', 5.0];
        yield 'padded' => [' 7 ', 7.0];
        yield 'negative' => ['-1', 0.0];
        yield 'invalid' => ['abc', null];
        yield 'empty' => ['', null];
        yield 'missing' => [null, null];
        yield 'future date' => ['Sun, 15 Nov 2026 10:00:30 GMT', 30.0];
        yield 'past date' => ['Sun, 15 Nov 2026 09:59:00 GMT', 0.0];
        yield 'rfc 850 date' => ['Sunday, 15-Nov-26 10:00:10 GMT', 10.0];
        yield 'asctime date' => ['Sun Nov 15 10:00:20 2026', 20.0];
    }

    #[DataProvider('retryAfterValues')]
    public function testParseRetryAfter(?string $value, ?float $expected): void
    {
        $now = (float) gmmktime(10, 0, 0, 11, 15, 2026);

        self::assertSame($expected, Http::parseRetryAfter($value, $now));
    }
}
