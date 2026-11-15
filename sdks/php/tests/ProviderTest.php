<?php

declare(strict_types=1);

namespace MegooFlags\Tests;

use MegooFlags\Config;
use MegooFlags\Internal\Cache\CacheFiles;
use MegooFlags\Internal\Cache\Lock;
use MegooFlags\Internal\Cache\State;
use MegooFlags\Internal\Engine\Compiler;
use MegooFlags\Provider;
use MegooFlags\Tests\Support\FakeServer;
use MegooFlags\Tests\Support\HookStream;
use MegooFlags\Tests\Support\RulesetGen as G;
use OpenFeature\implementation\flags\Attributes;
use OpenFeature\implementation\flags\EvaluationContext;
use OpenFeature\implementation\provider\NoOpProvider;
use OpenFeature\interfaces\flags\Client;
use OpenFeature\interfaces\flags\EvaluationDetails;
use OpenFeature\OpenFeatureAPI;
use PHPUnit\Framework\TestCase;
use stdClass;

final class ProviderTest extends TestCase
{
    private const KEY = 'mf_server_provider-secret';

    private string $dir;

    private string $cacheDir;

    private ?FakeServer $server = null;

    protected function setUp(): void
    {
        $this->dir = sys_get_temp_dir() . '/megooflags-provider-' . bin2hex(random_bytes(6));
        mkdir($this->dir, 0o700);
        $this->cacheDir = $this->dir . '/cache';
    }

    protected function tearDown(): void
    {
        // Drop the provider, so that its shutdown flush does not run.
        OpenFeatureAPI::getInstance()->setProvider(new NoOpProvider());
        $this->server?->stop();
        self::removeTree($this->dir);
    }

    private static function removeTree(string $path): void
    {
        if (is_dir($path)) {
            foreach (scandir($path) ?: [] as $entry) {
                if ($entry !== '.' && $entry !== '..') {
                    self::removeTree($path . '/' . $entry);
                }
            }
            rmdir($path);
        } elseif (is_file($path)) {
            unlink($path);
        }
    }

    private function server(): FakeServer
    {
        return $this->server ??= FakeServer::start();
    }

    private function files(): CacheFiles
    {
        return new CacheFiles($this->cacheDir, self::KEY);
    }

    /**
     * tree is the test ruleset; $str is the value of flag "str" when no rule
     * matches.
     */
    private static function tree(string $str = 'fallthrough'): stdClass
    {
        return G::ruleset([
            G::flag('bool', [
                'type' => 'boolean',
                'variations' => [G::variation('on', true), G::variation('off', false)],
                'targets' => [G::target('off', ['user-9'])],
            ]),
            G::flag('str', [
                'variations' => [G::variation('pro', 'pro-value'), G::variation('free', $str)],
                'rules' => [G::rule('pro', G::clause('plan', 'in', ['pro']))],
                'off_variation' => 'free',
                'fallthrough' => G::serve('free'),
            ]),
            G::flag('num', [
                'type' => 'number',
                'variations' => [G::variation('three', 3), G::variation('half', 2.5), G::variation('three-f', 3.0)],
                'targets' => [G::target('half', ['half-user']), G::target('three-f', ['float-user'])],
                'off_variation' => 'three',
                'fallthrough' => G::serve('three'),
            ]),
            G::flag('obj', [
                'type' => 'json',
                'variations' => [
                    G::variation('map', (object) ['a' => 1, 'b' => [1, 2]]),
                    G::variation('list', [1, 2]),
                    G::variation('scalar', 'x'),
                ],
                'targets' => [G::target('list', ['list-user']), G::target('scalar', ['scalar-user'])],
                'off_variation' => 'map',
                'fallthrough' => G::serve('map'),
            ]),
            G::flag('gated', [
                'prerequisites' => [G::prerequisite('bool', 'off')],
            ]),
        ]);
    }

    private static function raw(stdClass $tree): string
    {
        return (string) json_encode($tree, JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION);
    }

    private function bootstrapFile(string $str = 'boot'): string
    {
        $path = $this->dir . '/bootstrap.json';
        file_put_contents($path, self::raw(self::tree($str)));

        return $path;
    }

    /** cacheNetwork writes both cache files and a state that is not due for upkeep. */
    private function cacheNetwork(string $str = 'cached', string $etag = '"e1"', ?State $state = null): void
    {
        $raw = self::raw(self::tree($str));
        $this->files()->saveNetwork($raw, $etag, Compiler::compileJson($raw));
        ($state ?? new State(checkedAt: microtime(true), etag: $etag))->save($this->files()->statePath());
    }

    /** @param array<string, mixed> $overrides */
    private function config(array $overrides = []): Config
    {
        $args = array_merge(['sdkKey' => self::KEY, 'baseUrl' => $this->server()->url(), 'cacheDir' => $this->cacheDir], $overrides);

        return new Config(...$args);
    }

    private function offlineConfig(): Config
    {
        return new Config(sdkKey: self::KEY, cacheDir: $this->cacheDir, bootstrapFile: $this->bootstrapFile(), offline: true);
    }

    private static function client(Provider $provider): Client
    {
        $api = OpenFeatureAPI::getInstance();
        $api->setProvider($provider);

        return $api->getClient('provider-test');
    }

    private static function errorCode(EvaluationDetails $details): ?string
    {
        $code = $details->getError()?->getResolutionErrorCode()->getValue();

        return is_string($code) ? $code : null;
    }

    private static function assertError(EvaluationDetails $details, mixed $default, string $code): void
    {
        self::assertSame($default, $details->getValue());
        self::assertSame('ERROR', $details->getReason());
        self::assertSame($code, self::errorCode($details));
        self::assertNull($details->getVariant());
    }

    /** @return list<array{method: string, path: string, query: string, headers: array<string, string>, body: string}> */
    private function requests(?string $method = null): array
    {
        if ($this->server === null) {
            return [];
        }

        return array_values(array_filter(
            $this->server->requests(),
            static fn(array $r): bool => $method === null || $r['method'] === $method,
        ));
    }

    /** @return list<string> */
    private function spoolLines(): array
    {
        $path = $this->files()->spoolPath();

        return is_file($path) ? (file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: []) : [];
    }

    // Evaluation through the OpenFeature client.

    public function testBoolean(): void
    {
        $details = self::client(new Provider($this->offlineConfig()))->getBooleanDetails('bool', false);

        self::assertTrue($details->getValue());
        self::assertSame('on', $details->getVariant());
        self::assertSame('DEFAULT', $details->getReason());
        self::assertNull($details->getError());
    }

    public function testStringWithRuleIndexMetadata(): void
    {
        $client = self::client(new Provider($this->offlineConfig()));

        $details = $client->getStringDetails('str', 'default', new EvaluationContext('u1', new Attributes(['plan' => 'pro'])));

        self::assertSame('pro-value', $details->getValue());
        self::assertSame('pro', $details->getVariant());
        self::assertSame('TARGETING_MATCH', $details->getReason());
        self::assertSame(['rule_index' => 0], $details->getMetadata());

        $fallthrough = $client->getStringDetails('str', 'default');
        self::assertSame('boot', $fallthrough->getValue());
        self::assertSame([], $fallthrough->getMetadata());
    }

    public function testIntegerIntegral(): void
    {
        $client = self::client(new Provider($this->offlineConfig()));

        self::assertSame(3, $client->getIntegerDetails('num', 7)->getValue());
        $float = $client->getIntegerDetails('num', 7, new EvaluationContext('float-user'));
        self::assertSame(3, $float->getValue());
        self::assertSame('three-f', $float->getVariant());
    }

    public function testIntegerNotIntegralIsATypeMismatch(): void
    {
        $details = self::client(new Provider($this->offlineConfig()))->getIntegerDetails('num', 7, new EvaluationContext('half-user'));

        self::assertError($details, 7, 'TYPE_MISMATCH');
    }

    public function testFloatReturnsAnIntegerValueAsFloat(): void
    {
        $client = self::client(new Provider($this->offlineConfig()));

        self::assertSame(3.0, $client->getFloatDetails('num', 1.5)->getValue());
        self::assertSame(2.5, $client->getFloatDetails('num', 1.5, new EvaluationContext('half-user'))->getValue());
    }

    public function testObjectValuesAreArrays(): void
    {
        $client = self::client(new Provider($this->offlineConfig()));

        $map = $client->getObjectDetails('obj', ['d' => true]);
        self::assertSame(['a' => 1, 'b' => [1, 2]], $map->getValue());
        self::assertSame('map', $map->getVariant());
        self::assertSame([1, 2], $client->getObjectDetails('obj', [], new EvaluationContext('list-user'))->getValue());
    }

    public function testObjectWithAScalarValueIsATypeMismatch(): void
    {
        $details = self::client(new Provider($this->offlineConfig()))->getObjectDetails('obj', ['d' => true], new EvaluationContext('scalar-user'));

        self::assertError($details, ['d' => true], 'TYPE_MISMATCH');
    }

    public function testFlagNotFound(): void
    {
        $details = self::client(new Provider($this->offlineConfig()))->getStringDetails('missing', 'default');

        self::assertError($details, 'default', 'FLAG_NOT_FOUND');
        self::assertStringContainsString('missing', (string) $details->getError()?->getResolutionErrorMessage());
    }

    public function testTypeMismatch(): void
    {
        $details = self::client(new Provider($this->offlineConfig()))->getBooleanDetails('str', true);

        self::assertError($details, true, 'TYPE_MISMATCH');
    }

    public function testTargetingKeyBecomesKey(): void
    {
        $client = self::client(new Provider($this->offlineConfig()));

        $details = $client->getBooleanDetails('bool', true, new EvaluationContext('user-9', new Attributes(['key' => 'someone-else'])));

        self::assertFalse($details->getValue());
        self::assertSame('TARGETING_MATCH', $details->getReason());
        self::assertTrue($client->getBooleanDetails('bool', false, new EvaluationContext(null, new Attributes(['key' => 'user-1'])))->getValue());
        self::assertFalse($client->getBooleanDetails('bool', true, new EvaluationContext(null, new Attributes(['key' => 'user-9'])))->getValue());
    }

    public function testPrerequisiteFailedWithMetadata(): void
    {
        $details = self::client(new Provider($this->offlineConfig()))->getStringDetails('gated', 'default');

        self::assertSame('off', $details->getValue());
        self::assertSame('off', $details->getVariant());
        self::assertSame('PREREQUISITE_FAILED', $details->getReason());
        self::assertSame(['prerequisite' => 'bool'], $details->getMetadata());
        self::assertNull($details->getError());
    }

    public function testClientKeyGivesTheDefaultWithProviderNotReady(): void
    {
        $this->cacheNetwork();
        $details = self::client(new Provider($this->config(['sdkKey' => 'mf_client_abc'])))->getStringDetails('str', 'default');

        self::assertError($details, 'default', 'PROVIDER_NOT_READY');
        self::assertStringContainsString('client SDK key', (string) $details->getError()?->getResolutionErrorMessage());
        self::assertSame([], $this->requests());
    }

    public function testInvalidConfigGivesTheDefaultWithProviderNotReady(): void
    {
        $details = self::client(new Provider($this->config(['baseUrl' => 'ftp://nope'])))->getBooleanDetails('bool', true);

        self::assertError($details, true, 'PROVIDER_NOT_READY');
        self::assertSame('megooflags: baseUrl must be an http or https URL', $details->getError()?->getResolutionErrorMessage());
    }

    // Load order.

    public function testCompiledCacheWinsOverTheJsonCacheAndTheBootstrapFile(): void
    {
        $this->cacheNetwork('json');
        $this->files()->saveCompiledFromJson('"e1"', Compiler::compile(self::tree('compiled')));

        $provider = new Provider($this->config(['bootstrapFile' => $this->bootstrapFile()]));

        self::assertSame('compiled', self::client($provider)->getStringValue('str', 'default'));
        self::assertSame([], $this->requests());
    }

    public function testJsonCacheIsCompiledWhenThereIsNoCompiledFile(): void
    {
        $this->cacheNetwork('json');
        unlink($this->files()->compiledPath());

        $provider = new Provider($this->config(['bootstrapFile' => $this->bootstrapFile()]));

        self::assertSame('json', self::client($provider)->getStringValue('str', 'default'));
        $compiled = $this->files()->loadCompiled();
        self::assertNotNull($compiled);
        self::assertSame('"e1"', $compiled['etag']);
        self::assertSame('network', $compiled['source']);
        self::assertSame([], $this->requests());
    }

    public function testCorruptCompiledFileIsIgnored(): void
    {
        $this->cacheNetwork('json');
        file_put_contents($this->files()->compiledPath(), "<?php\n\nreturn ['etag' => ");

        self::assertSame('json', self::client(new Provider($this->config()))->getStringValue('str', 'default'));
    }

    public function testBootstrapFileWhenThereIsNoCache(): void
    {
        $provider = new Provider($this->config(['bootstrapFile' => $this->bootstrapFile()]));

        self::assertSame('boot', self::client($provider)->getStringValue('str', 'default'));
        self::assertSame(['etag' => '', 'source' => 'bootstrap'], array_slice((array) $this->files()->loadCompiled(), 0, 2));
        self::assertFileDoesNotExist($this->files()->jsonPath());
        self::assertSame([], $this->requests());
    }

    public function testBootstrapDoesNotOverwriteACacheWrittenAfterItWasChosen(): void
    {
        // The provider finds no cache and reads the bootstrap file; while it
        // does, another process's upkeep writes both cache files and the state.
        $bootstrap = HookStream::register(self::raw(self::tree('boot')), function (): void {
            $this->cacheNetwork('network', '"e1"');
        });
        try {
            $value = self::client(new Provider($this->config(['bootstrapFile' => $bootstrap])))->getStringValue('str', 'default');
        } finally {
            HookStream::unregister();
        }

        self::assertSame('network', $value);
        $compiled = $this->files()->loadCompiled(true);
        self::assertSame(['etag' => '"e1"', 'source' => 'network'], array_slice((array) $compiled, 0, 2));
        self::assertSame('network', self::client(new Provider($this->config()))->getStringValue('str', 'default'));
        self::assertSame([], $this->requests());
    }

    public function testCompiledFileOlderThanTheStateIsRebuiltFromTheJsonCache(): void
    {
        // A bootstrap write landed over the compiled file of etag e1: the
        // state and the JSON cache say e1, so the conditional fetch would
        // get 304 and never repair it.
        $this->cacheNetwork('network', '"e1"');
        $this->files()->saveBootstrap(Compiler::compile(self::tree('boot')));

        $value = self::client(new Provider($this->config(['bootstrapFile' => $this->bootstrapFile()])))->getStringValue('str', 'default');

        self::assertSame('network', $value);
        self::assertSame(['etag' => '"e1"', 'source' => 'network'], array_slice((array) $this->files()->loadCompiled(true), 0, 2));
        self::assertSame([], $this->requests());
    }

    public function testCompiledFileNewerThanTheStateIsServed(): void
    {
        // Upkeep wrote both cache files of e2 and has not written the state yet.
        $this->cacheNetwork('one', '"e1"');
        $raw = self::raw(self::tree('two'));
        $this->files()->saveNetwork($raw, '"e2"', Compiler::compileJson($raw));

        self::assertSame('two', self::client(new Provider($this->config()))->getStringValue('str', 'default'));
        self::assertSame('"e2"', $this->files()->loadCompiled()['etag'] ?? null);
    }

    public function testBootstrapIsNotWrittenWhileAnotherProcessHoldsTheLock(): void
    {
        $lock = new Lock($this->files()->lockPath());
        self::assertTrue($lock->tryAcquire());

        $value = self::client(new Provider($this->config(['bootstrapFile' => $this->bootstrapFile()])))->getStringValue('str', 'default');

        $lock->release();
        self::assertSame('boot', $value);
        self::assertFileDoesNotExist($this->files()->compiledPath());
        self::assertSame([], $this->requests());
    }

    public function testJsonCacheIsNotCompiledToDiskWhileAnotherProcessHoldsTheLock(): void
    {
        $this->cacheNetwork('json');
        unlink($this->files()->compiledPath());
        $lock = new Lock($this->files()->lockPath());
        self::assertTrue($lock->tryAcquire());

        $value = self::client(new Provider($this->config()))->getStringValue('str', 'default');

        $lock->release();
        self::assertSame('json', $value);
        self::assertFileDoesNotExist($this->files()->compiledPath());
    }

    public function testInvalidBootstrapFileLeavesTheProviderNotReady(): void
    {
        $path = $this->dir . '/bad.json';
        file_put_contents($path, '{"schema_version": 1}');
        $config = new Config(sdkKey: self::KEY, cacheDir: $this->cacheDir, bootstrapFile: $path, offline: true);

        self::assertError(self::client(new Provider($config))->getStringDetails('str', 'default'), 'default', 'PROVIDER_NOT_READY');
    }

    public function testColdStartFetchesTheRuleset(): void
    {
        $this->server()->respond('GET', '/sdk/v1/ruleset', ['status' => 200, 'headers' => ['ETag' => '"v9"'], 'body' => self::raw(self::tree('fetched'))]);
        $before = microtime(true);

        self::assertSame('fetched', self::client(new Provider($this->config()))->getStringValue('str', 'default'));

        $get = $this->requests('GET');
        self::assertCount(1, $get);
        self::assertSame('Bearer ' . self::KEY, $get[0]['headers']['authorization']);
        self::assertArrayNotHasKey('if-none-match', $get[0]['headers']);
        self::assertSame('"v9"', $this->files()->loadJson()['etag'] ?? null);
        self::assertSame('"v9"', $this->files()->loadCompiled()['etag'] ?? null);
        $state = State::load($this->files()->statePath());
        self::assertSame('"v9"', $state->etag);
        self::assertGreaterThanOrEqual($before, $state->checkedAt);
        self::assertSame(0, $state->failures);
    }

    public function testColdStartIsBoundedByInitTimeout(): void
    {
        $this->server()->respond('GET', '/sdk/v1/ruleset', ['status' => 200, 'delay' => 3, 'body' => self::raw(self::tree())]);
        $start = microtime(true);

        $details = self::client(new Provider($this->config(['initTimeout' => 0.3])))->getStringDetails('str', 'default');

        self::assertError($details, 'default', 'PROVIDER_NOT_READY');
        self::assertLessThan(2.0, microtime(true) - $start);
    }

    public function testColdStartWaitsForTheLockAtMostInitTimeout(): void
    {
        $lock = new Lock($this->files()->lockPath());
        self::assertTrue($lock->tryAcquire());
        $start = microtime(true);

        $details = self::client(new Provider($this->config(['initTimeout' => 0.3])))->getStringDetails('str', 'default');

        $elapsed = microtime(true) - $start;
        self::assertError($details, 'default', 'PROVIDER_NOT_READY');
        self::assertGreaterThanOrEqual(0.3, $elapsed);
        self::assertLessThan(2.0, $elapsed);
        self::assertSame([], $this->requests());
        $lock->release();
    }

    public function testColdStartUsesTheCompiledFileWrittenWhileWaitingForTheLock(): void
    {
        // Another process holds the lock, writes the cache, then releases it.
        $vars = var_export(['key' => self::KEY, 'dir' => $this->cacheDir, 'raw' => self::raw(self::tree('other-process')), 'held' => $this->dir . '/held'], true);
        $child = $this->startPhp(<<<PHP
            \$v = {$vars};
            \$files = new \\MegooFlags\\Internal\\Cache\\CacheFiles(\$v['dir'], \$v['key']);
            \$lock = new \\MegooFlags\\Internal\\Cache\\Lock(\$files->lockPath());
            \$lock->tryAcquire() || exit(1);
            touch(\$v['held']);
            usleep(300_000);
            \$files->saveNetwork(\$v['raw'], '"p2"', \\MegooFlags\\Internal\\Engine\\Compiler::compileJson(\$v['raw']));
            \$lock->release();
            PHP);
        $deadline = microtime(true) + 5.0;
        while (!is_file($this->dir . '/held') && microtime(true) < $deadline) {
            usleep(10_000);
        }
        self::assertFileExists($this->dir . '/held');

        $value = self::client(new Provider($this->config(['initTimeout' => 3.0])))->getStringValue('str', 'default');

        self::assertSame(0, proc_close($child));
        self::assertSame('other-process', $value);
        self::assertSame([], $this->requests());
    }

    public function testFailedColdStartSetsNextAttemptAndTheNextProviderSendsNoRequest(): void
    {
        $this->server()->respond('GET', '/sdk/v1/ruleset', ['status' => 500]);
        $before = microtime(true);

        $details = self::client(new Provider($this->config()))->getStringDetails('str', 'default');

        self::assertError($details, 'default', 'PROVIDER_NOT_READY');
        self::assertStringContainsString('HTTP 500', (string) $details->getError()?->getResolutionErrorMessage());
        $state = State::load($this->files()->statePath());
        self::assertSame(1, $state->failures);
        self::assertGreaterThan($before, $state->nextAttempt);
        self::assertCount(1, $this->requests());

        $again = self::client(new Provider($this->config()))->getStringDetails('str', 'default');

        self::assertError($again, 'default', 'PROVIDER_NOT_READY');
        self::assertCount(1, $this->requests());
    }

    public function testRefusedKeyAtColdStartWritesTheFatalMarker(): void
    {
        $this->server()->respond('GET', '/sdk/v1/ruleset', ['status' => 401]);

        $details = self::client(new Provider($this->config()))->getStringDetails('str', 'default');

        self::assertError($details, 'default', 'PROVIDER_NOT_READY');
        self::assertSame('megooflags: SDK key refused (401)', $details->getError()?->getResolutionErrorMessage());
        self::assertSame('401', State::load($this->files()->statePath())->fatal);

        self::assertError(self::client(new Provider($this->config()))->getStringDetails('str', 'default'), 'default', 'PROVIDER_NOT_READY');
        self::assertCount(1, $this->requests());
    }

    // Offline, fatal marker and flush.

    public function testOfflineNeverOpensAConnectionAndWritesNoSpoolLine(): void
    {
        $this->cacheNetwork('cached', '"e1"', new State());
        $provider = new Provider($this->config(['offline' => true]));

        self::assertSame('cached', self::client($provider)->getStringValue('str', 'default'));
        $provider->flush();

        self::assertSame([], $this->requests());
        self::assertSame([], $this->spoolLines());
    }

    public function testOfflineWithoutARulesetIsNotReadyWithoutARequest(): void
    {
        $provider = new Provider($this->config(['offline' => true]));

        self::assertError(self::client($provider)->getStringDetails('str', 'default'), 'default', 'PROVIDER_NOT_READY');
        self::assertSame([], $this->requests());
    }

    public function testFatalMarkerWithACachedRulesetServesItAndSendsNothing(): void
    {
        $this->cacheNetwork('cached', '"e1"', new State(etag: '"e1"', fatal: '403'));
        $provider = new Provider($this->config());

        self::assertSame('cached', self::client($provider)->getStringValue('str', 'default'));
        $provider->flush();

        self::assertSame([], $this->requests());
        self::assertSame([], $this->spoolLines());
    }

    public function testFlushAppendsOneSpoolLine(): void
    {
        $this->cacheNetwork();
        $provider = new Provider($this->config());
        $client = self::client($provider);
        $client->getStringValue('str', 'default', new EvaluationContext('u1'));
        $client->getStringValue('str', 'default', new EvaluationContext('u1'));
        $client->getStringValue('missing', 'default', new EvaluationContext('u1'));

        $provider->flush();

        $lines = $this->spoolLines();
        self::assertCount(1, $lines);
        $line = json_decode($lines[0], true, 512, JSON_THROW_ON_ERROR);
        self::assertIsArray($line);
        $hour = intdiv(time(), 3600) * 3600;
        self::assertEqualsCanonicalizing([['str', 'free', $hour, 2], ['missing', null, $hour, 1]], $line['counts']);
        self::assertCount(1, $line['exposures']);
        self::assertSame(['flag_key' => 'str', 'variant' => 'free', 'reason' => 'DEFAULT'], array_slice($line['exposures'][0], 0, 3));
        self::assertSame('u1', $line['exposures'][0]['context_key']);
        self::assertSame([], $this->requests(), 'upkeep is not due');

        $provider->flush();

        self::assertCount(1, $this->spoolLines());
    }

    public function testFlushRunsUpkeepWhenItIsDue(): void
    {
        $this->cacheNetwork('cached', '"e1"', new State(checkedAt: 0.0, etag: '"e1"'));
        $this->server()->respond('GET', '/sdk/v1/ruleset', ['status' => 304]);
        $this->server()->respond('POST', '/sdk/v1/events', ['status' => 202]);
        $provider = new Provider($this->config());
        self::client($provider)->getStringValue('str', 'default', new EvaluationContext('u1'));
        $before = microtime(true);

        $provider->flush();

        $get = $this->requests('GET');
        self::assertCount(1, $get);
        self::assertSame('"e1"', $get[0]['headers']['if-none-match'] ?? null);
        $post = $this->requests('POST');
        self::assertCount(1, $post);
        $body = json_decode($post[0]['body'], true, 512, JSON_THROW_ON_ERROR);
        self::assertIsArray($body);
        self::assertSame('str', $body['counts'][0]['flag_key']);
        self::assertSame([], $this->spoolLines());
        self::assertGreaterThanOrEqual($before, State::load($this->files()->statePath())->checkedAt);

        $provider->flush();

        self::assertCount(1, $this->requests('GET'), 'upkeep is not due again');
    }

    public function testNoFileIsReadAfterTheFirstEvaluation(): void
    {
        $this->cacheNetwork();
        $client = self::client(new Provider($this->config()));
        self::assertSame('cached', $client->getStringValue('str', 'default'));

        self::removeTree($this->cacheDir);

        self::assertSame('cached', $client->getStringValue('str', 'default'));
        self::assertTrue($client->getBooleanValue('bool', false));
        self::assertSame([], $this->requests());
    }

    // Behaviour that needs a fresh PHP process.

    /**
     * runPhp runs $code (after the Composer autoloader) in a child PHP
     * process and returns its output; it fails the test on a non-zero exit.
     *
     * @param list<string> $ini
     */
    private function runPhp(string $code, array $ini = []): string
    {
        $process = $this->startPhp($code, $ini, $pipes);
        $out = (string) stream_get_contents($pipes[1]);
        $err = (string) stream_get_contents($pipes[2]);
        fclose($pipes[1]);
        fclose($pipes[2]);
        $status = proc_close($process);
        self::assertSame(0, $status, $out . $err);

        return $out;
    }

    /**
     * startPhp starts $code (after the Composer autoloader) in a child PHP
     * process; stdout and stderr are pipes 1 and 2 of $pipes, or the
     * parent's when $pipes is not passed.
     *
     * @param list<string> $ini
     * @param array<int, resource>|null $pipes
     * @return resource
     */
    private function startPhp(string $code, array $ini = [], ?array &$pipes = null)
    {
        $script = $this->dir . '/child-' . bin2hex(random_bytes(4)) . '.php';
        file_put_contents($script, "<?php\n\ndeclare(strict_types=1);\n\nrequire " . var_export(dirname(__DIR__) . '/vendor/autoload.php', true) . ";\n\n" . $code);
        $command = [PHP_BINARY];
        foreach ($ini as $setting) {
            $command[] = '-d';
            $command[] = $setting;
        }
        $command[] = $script;
        $capture = func_num_args() >= 3;
        $process = proc_open($command, $capture ? [1 => ['pipe', 'w'], 2 => ['pipe', 'w']] : [], $pipes);
        self::assertIsResource($process);

        return $process;
    }

    public function testFirstEvaluationRegistersFlushAtShutdown(): void
    {
        $this->cacheNetwork();
        $config = var_export(['sdkKey' => self::KEY, 'baseUrl' => $this->server()->url(), 'cacheDir' => $this->cacheDir], true);

        $this->runPhp(<<<PHP
            \$provider = new \\MegooFlags\\Provider(new \\MegooFlags\\Config(...{$config}));
            \$api = \\OpenFeature\\OpenFeatureAPI::getInstance();
            \$api->setProvider(\$provider);
            \$api->getClient()->getStringValue('str', 'default');
            \$api->getClient()->getStringValue('str', 'default');
            PHP);

        $lines = $this->spoolLines();
        self::assertCount(1, $lines);
        self::assertStringContainsString('"str","free"', $lines[0]);
    }

    public function testStateEtagDifferentFromTheCompiledEtagReloadsTheCompiledFile(): void
    {
        $this->cacheNetwork('one', '"e1"');
        $vars = var_export([
            'key' => self::KEY,
            'dir' => $this->cacheDir,
            'two' => Compiler::compile(self::tree('two')),
            'three' => Compiler::compile(self::tree('three')),
        ], true);

        // OPcache keeps the first file it compiled (no timestamp checks); the
        // compiled file is then replaced in place, without invalidating it.
        $out = $this->runPhp(<<<PHP
            \$v = {$vars};
            \$files = new \\MegooFlags\\Internal\\Cache\\CacheFiles(\$v['dir'], \$v['key']);
            \$value = static fn(): string => (string) (new \\MegooFlags\\Provider(new \\MegooFlags\\Config(sdkKey: \$v['key'], cacheDir: \$v['dir'], offline: true)))
                ->resolveStringValue('str', 'default')->getValue();
            \$replace = static function (string \$etag, array \$ruleset) use (\$files): void {
                \$data = ['etag' => \$etag, 'source' => 'network', 'ruleset' => \$ruleset];
                file_put_contents(\$files->compiledPath(), "<?php\\n\\nreturn " . var_export(\$data, true) . ";\\n");
            };
            \$cached = (opcache_get_status(false) ?: [])['opcache_enabled'] ?? false;
            echo \$cached ? 'opcache' : 'no-opcache', "\\n";
            echo \$value(), "\\n";
            \$replace('"e1"', \$v['two']);
            echo \$value(), "\\n";
            \$replace('"e2"', \$v['three']);
            (new \\MegooFlags\\Internal\\Cache\\State(etag: '"e2"'))->save(\$files->statePath());
            echo \$value(), "\\n";
            PHP, ['opcache.enable=1', 'opcache.enable_cli=1', 'opcache.validate_timestamps=0', 'opcache.file_update_protection=0']);

        $lines = explode("\n", trim($out));
        if ($lines[0] !== 'opcache') {
            self::markTestSkipped('OPcache is not available in the PHP CLI');
        }
        self::assertSame('one', $lines[1]);
        self::assertSame('one', $lines[2], 'the same etag keeps the OPcache copy');
        self::assertSame('three', $lines[3], 'a new state etag reloads the compiled file');
    }
}
