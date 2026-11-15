<?php

declare(strict_types=1);

namespace MegooFlags\Tests;

use MegooFlags\Config;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Psr\Log\AbstractLogger;
use Psr\Log\NullLogger;

final class ConfigTest extends TestCase
{
    private const KEY = 'mf_server_config-secret';

    private static function valid(string $sdkKey = self::KEY, string $baseUrl = 'https://flags.example.com', string $cacheDir = '/tmp/mf'): Config
    {
        return new Config(sdkKey: $sdkKey, baseUrl: $baseUrl, cacheDir: $cacheDir);
    }

    public function testDefaults(): void
    {
        $config = self::valid();

        self::assertNull($config->error());
        self::assertSame(self::KEY, $config->sdkKey);
        self::assertSame('https://flags.example.com', $config->baseUrl);
        self::assertSame('/tmp/mf', $config->cacheDir);
        self::assertNull($config->bootstrapFile);
        self::assertFalse($config->offline);
        self::assertSame(5.0, $config->initTimeout);
        self::assertFalse($config->disableStream);
        self::assertSame(30.0, $config->pollInterval);
        self::assertSame(30.0, $config->flushInterval);
        self::assertFalse($config->sendContext);
        self::assertInstanceOf(NullLogger::class, $config->logger);
    }

    public function testTrailingSlashesAreRemovedFromTheBaseUrl(): void
    {
        self::assertSame('https://flags.example.com/base', self::valid(baseUrl: 'https://flags.example.com/base//')->baseUrl);
    }

    public function testKeepsTheGivenLogger(): void
    {
        $logger = new class extends AbstractLogger {
            public function log($level, \Stringable|string $message, array $context = []): void {}
        };

        self::assertSame($logger, (new Config(sdkKey: self::KEY, baseUrl: 'http://x', cacheDir: '/tmp/mf', logger: $logger))->logger);
    }

    /** @return iterable<string, array{float, float}> */
    public static function pollIntervals(): iterable
    {
        yield 'raised to 15 s' => [5.0, 15.0];
        yield 'kept at 15 s' => [15.0, 15.0];
        yield 'kept above' => [120.0, 120.0];
        yield 'zero is the default' => [0.0, 30.0];
        yield 'negative is the default' => [-1.0, 30.0];
        yield 'NaN is the default' => [NAN, 30.0];
    }

    #[DataProvider('pollIntervals')]
    public function testPollIntervalIsAtLeast15Seconds(float $given, float $want): void
    {
        $config = new Config(sdkKey: self::KEY, baseUrl: 'http://x', cacheDir: '/tmp/mf', pollInterval: $given);

        self::assertSame($want, $config->pollInterval);
    }

    /** @return iterable<string, array{float, float}> */
    public static function flushIntervals(): iterable
    {
        yield 'raised to 10 s' => [1.0, 10.0];
        yield 'kept inside' => [45.0, 45.0];
        yield 'lowered to 60 s' => [600.0, 60.0];
        yield 'zero is the default' => [0.0, 30.0];
        yield 'infinite is the default' => [INF, 30.0];
    }

    #[DataProvider('flushIntervals')]
    public function testFlushIntervalIsClampedTo10To60Seconds(float $given, float $want): void
    {
        $config = new Config(sdkKey: self::KEY, baseUrl: 'http://x', cacheDir: '/tmp/mf', flushInterval: $given);

        self::assertSame($want, $config->flushInterval);
    }

    public function testInitTimeoutZeroOrNegativeIsTheDefault(): void
    {
        self::assertSame(0.25, (new Config(sdkKey: self::KEY, baseUrl: 'http://x', cacheDir: '/tmp/mf', initTimeout: 0.25))->initTimeout);
        self::assertSame(5.0, (new Config(sdkKey: self::KEY, baseUrl: 'http://x', cacheDir: '/tmp/mf', initTimeout: 0.0))->initTimeout);
        self::assertSame(5.0, (new Config(sdkKey: self::KEY, baseUrl: 'http://x', cacheDir: '/tmp/mf', initTimeout: -2.0))->initTimeout);
    }

    public function testClientKeyIsRefused(): void
    {
        $error = self::valid(sdkKey: 'mf_client_abc')->error();

        self::assertSame('megooflags: a client SDK key cannot be used by a server SDK; use a server key (mf_server_…)', $error);
    }

    public function testKeyThatIsNotAServerKeyIsRefused(): void
    {
        self::assertSame('megooflags: invalid SDK key; use a server key (mf_server_…)', self::valid(sdkKey: 'secret-abc')->error());
        self::assertSame('megooflags: invalid SDK key; use a server key (mf_server_…)', self::valid(sdkKey: '')->error());
    }

    public function testCacheDirIsRequired(): void
    {
        self::assertSame('megooflags: cacheDir is required', self::valid(cacheDir: '')->error());
        self::assertSame(
            'megooflags: cacheDir is required',
            (new Config(sdkKey: self::KEY, cacheDir: '', bootstrapFile: '/tmp/b.json', offline: true))->error(),
        );
    }

    public function testOfflineNeedsNoBaseUrl(): void
    {
        self::assertNull((new Config(sdkKey: self::KEY, cacheDir: '/tmp/mf', offline: true))->error());
        self::assertNull((new Config(sdkKey: self::KEY, baseUrl: 'not a url', cacheDir: '/tmp/mf', offline: true))->error());
    }

    /** @return iterable<string, array{string}> */
    public static function badBaseUrls(): iterable
    {
        yield 'empty' => [''];
        yield 'no scheme' => ['flags.example.com'];
        yield 'ftp' => ['ftp://flags.example.com'];
        yield 'no host' => ['http://'];
        yield 'path only' => ['/sdk'];
    }

    #[DataProvider('badBaseUrls')]
    public function testBaseUrlMustBeHttpOrHttpsWithAHost(string $baseUrl): void
    {
        self::assertSame('megooflags: baseUrl must be an http or https URL', self::valid(baseUrl: $baseUrl)->error());
    }

    public function testGoodBaseUrls(): void
    {
        self::assertNull(self::valid(baseUrl: 'http://127.0.0.1:8080')->error());
        self::assertNull(self::valid(baseUrl: 'HTTPS://flags.example.com/')->error());
    }

    public function testMessagesAndDumpsDoNotContainTheKey(): void
    {
        foreach (['mf_client_secret-1', 'other-secret-2', 'mf_server_secret-3'] as $key) {
            $config = new Config(sdkKey: $key, baseUrl: 'nope', cacheDir: '');
            self::assertStringNotContainsString($key, (string) $config->error());
            self::assertStringNotContainsString($key, print_r($config, true));
            self::assertStringNotContainsString($key, var_export($config->__debugInfo(), true));
        }
    }
}
