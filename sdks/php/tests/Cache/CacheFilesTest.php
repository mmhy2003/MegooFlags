<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Cache;

use MegooFlags\Internal\Cache\CacheFiles;
use MegooFlags\Internal\Engine\Compiler;
use MegooFlags\Tests\Support\RulesetGen as G;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

final class CacheFilesTest extends TestCase
{
    private const KEY = 'srv-secret-key-123';

    private string $dir;

    protected function setUp(): void
    {
        $this->dir = sys_get_temp_dir() . '/megooflags-cache-' . bin2hex(random_bytes(6));
    }

    protected function tearDown(): void
    {
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

    private function files(): CacheFiles
    {
        return new CacheFiles($this->dir, self::KEY);
    }

    private static function rawRuleset(): string
    {
        $tree = G::ruleset([G::flag('f', ['rules' => [G::rule('on', G::clause('email', 'matches', ['^a.*@x\.io$']))]])]);

        return (string) json_encode($tree, JSON_UNESCAPED_SLASHES);
    }

    /** @return list<string> */
    private function entries(): array
    {
        return array_values(array_diff(scandir($this->dir) ?: [], ['.', '..']));
    }

    public function testFileNamesUseTheKeyHash(): void
    {
        $hash = substr(hash('sha256', self::KEY), 0, 16);
        $files = $this->files();

        self::assertSame($this->dir . '/megooflags-' . $hash . '.json', $files->jsonPath());
        self::assertSame($this->dir . '/megooflags-' . $hash . '.php', $files->compiledPath());
        self::assertSame($this->dir . '/megooflags-' . $hash . '.state.json', $files->statePath());
        self::assertSame($this->dir . '/megooflags-' . $hash . '.lock', $files->lockPath());
        self::assertSame($this->dir . '/megooflags-' . $hash . '.events.jsonl', $files->spoolPath());
        foreach ([$files->jsonPath(), $files->compiledPath(), $files->statePath(), $files->lockPath(), $files->spoolPath()] as $path) {
            self::assertStringNotContainsString(self::KEY, $path);
        }
    }

    public function testNetworkSaveWritesBothFilesInTheCacheFormat(): void
    {
        $raw = self::rawRuleset();
        $compiled = Compiler::compileJson($raw);

        $this->files()->saveNetwork($raw, '"v1"', $compiled);

        $file = json_decode((string) file_get_contents($this->files()->jsonPath()), false, 512, JSON_THROW_ON_ERROR);
        self::assertSame(['etag', 'ruleset'], array_keys(get_object_vars($file)));
        self::assertSame('"v1"', $file->etag);
        self::assertEquals(json_decode($raw, false), $file->ruleset);

        $json = $this->files()->loadJson();
        self::assertNotNull($json);
        self::assertSame('"v1"', $json['etag']);
        self::assertEquals(json_decode($raw, false), $json['ruleset']);

        $loaded = $this->files()->loadCompiled();
        self::assertSame(['etag' => '"v1"', 'source' => 'network', 'ruleset' => $compiled], $loaded);
        self::assertStringStartsWith('<?php', (string) file_get_contents($this->files()->compiledPath()));
    }

    public function testCompiledFileRoundTripsThroughRequire(): void
    {
        $compiled = Compiler::compileJson(self::rawRuleset());
        $this->files()->saveNetwork(self::rawRuleset(), 'e', $compiled);

        $required = require $this->files()->compiledPath();

        self::assertSame(['etag' => 'e', 'source' => 'network', 'ruleset' => $compiled], $required);
    }

    public function testBootstrapSaveWritesOnlyTheCompiledFile(): void
    {
        $compiled = Compiler::compileJson(self::rawRuleset());

        $this->files()->saveBootstrap($compiled);

        self::assertFileDoesNotExist($this->files()->jsonPath());
        self::assertNull($this->files()->loadJson());
        self::assertSame(['etag' => '', 'source' => 'bootstrap', 'ruleset' => $compiled], $this->files()->loadCompiled());
    }

    public function testMissingFilesReturnNull(): void
    {
        self::assertNull($this->files()->loadJson());
        self::assertNull($this->files()->loadCompiled());
    }

    /**
     * @return iterable<string, array{string}>
     */
    public static function corruptJson(): iterable
    {
        yield 'not json' => ['nope'];
        yield 'truncated' => ['{"etag":"v1","ruleset":{"schema_version":1'];
        yield 'empty' => [''];
        yield 'array' => ['[]'];
        yield 'etag not a string' => ['{"etag":1,"ruleset":{}}'];
        yield 'no ruleset' => ['{"etag":"v1"}'];
    }

    #[DataProvider('corruptJson')]
    public function testCorruptJsonCacheIsIgnored(string $content): void
    {
        mkdir($this->dir);
        file_put_contents($this->files()->jsonPath(), $content);

        self::assertNull($this->files()->loadJson());
    }

    /**
     * @return iterable<string, array{string}>
     */
    public static function corruptCompiled(): iterable
    {
        yield 'truncated' => ["<?php return ['etag' => 'e', 'source' => 'network', 'ruleset' => ['project' => "];
        yield 'throws' => ["<?php throw new \\RuntimeException('boom');"];
        yield 'not an array' => ['<?php return 42;'];
        yield 'wrong shape' => ["<?php return ['etag' => 1, 'source' => 'network', 'ruleset' => []];"];
        yield 'unknown source' => ["<?php return ['etag' => 'e', 'source' => 'other', 'ruleset' => []];"];
        yield 'empty' => [''];
        yield 'plain text' => ['not php'];
    }

    #[DataProvider('corruptCompiled')]
    public function testCorruptCompiledFileIsIgnored(string $content): void
    {
        mkdir($this->dir);
        file_put_contents($this->files()->compiledPath(), $content);

        self::assertNull($this->files()->loadCompiled());
    }

    public function testWritesCreateTheDirectoryAndLeaveNoTempFile(): void
    {
        $this->dir .= '/nested/cache';
        $raw = self::rawRuleset();

        $this->files()->saveNetwork($raw, 'v1', Compiler::compileJson($raw));
        $this->files()->saveNetwork($raw, 'v2', Compiler::compileJson($raw));

        $hash = substr(hash('sha256', self::KEY), 0, 16);
        self::assertSame(['megooflags-' . $hash . '.json', 'megooflags-' . $hash . '.php'], $this->entries());
        self::assertSame(0o700, fileperms($this->dir) & 0o777);
        self::assertSame(0o600, fileperms($this->files()->jsonPath()) & 0o777);
        self::assertSame(0o600, fileperms($this->files()->compiledPath()) & 0o777);
        self::assertSame('v2', $this->files()->loadJson()['etag'] ?? null);
        self::assertSame('v2', $this->files()->loadCompiled()['etag'] ?? null);
    }

    public function testAtomicWriteReplacesAFile(): void
    {
        $path = $this->dir . '/x.json';

        CacheFiles::writeAtomic($path, 'one');
        CacheFiles::writeAtomic($path, 'two');

        self::assertSame('two', file_get_contents($path));
        self::assertSame(0o600, fileperms($path) & 0o777);
        self::assertSame(['x.json'], $this->entries());
    }

    public function testKeyIsNeverWrittenToDisk(): void
    {
        $raw = self::rawRuleset();
        $this->files()->saveNetwork($raw, 'v1', Compiler::compileJson($raw));

        foreach ($this->entries() as $entry) {
            self::assertStringNotContainsString(self::KEY, (string) file_get_contents($this->dir . '/' . $entry));
        }
    }
}
