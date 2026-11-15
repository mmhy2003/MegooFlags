<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Events;

use MegooFlags\Internal\Events\Spool;
use PHPUnit\Framework\TestCase;

final class SpoolTest extends TestCase
{
    private string $dir;

    private string $path;

    protected function setUp(): void
    {
        $this->dir = sys_get_temp_dir() . '/megooflags-spool-' . bin2hex(random_bytes(6));
        $this->path = $this->dir . '/megooflags-0123456789abcdef.events.jsonl';
    }

    protected function tearDown(): void
    {
        foreach (glob($this->dir . '/{,.}*', GLOB_BRACE) ?: [] as $file) {
            if (is_file($file)) {
                unlink($file);
            }
        }
        if (is_dir($this->dir)) {
            rmdir($this->dir);
        }
    }

    public function testAppendAddsOneLine(): void
    {
        $spool = new Spool($this->path);

        self::assertTrue($spool->append('{"counts":[],"exposures":[]}'));
        self::assertTrue($spool->append('{"counts":[["a","on",0,1]],"exposures":[]}'));

        self::assertSame(
            "{\"counts\":[],\"exposures\":[]}\n{\"counts\":[[\"a\",\"on\",0,1]],\"exposures\":[]}\n",
            file_get_contents($this->path),
        );
        self::assertSame(0o600, fileperms($this->path) & 0o777);
        self::assertSame(0o700, fileperms($this->dir) & 0o777);
    }

    public function testLineIsDroppedWhenTheFileIs8MiBOrLarger(): void
    {
        $spool = new Spool($this->path);
        $spool->append('x');
        $handle = fopen($this->path, 'r+');
        self::assertNotFalse($handle);
        ftruncate($handle, Spool::MAX_BYTES - 1);
        fclose($handle);

        self::assertTrue($spool->append('y')); // the file is below 8 MiB before this line
        clearstatcache();
        $size = (int) filesize($this->path);
        self::assertGreaterThanOrEqual(Spool::MAX_BYTES, $size);

        self::assertFalse($spool->append('z'));
        clearstatcache();
        self::assertSame($size, filesize($this->path));
        self::assertSame(8 * 1024 * 1024, Spool::MAX_BYTES);
    }

    public function testClaimRenamesAtomicallyAndASecondClaimFindsNothing(): void
    {
        $spool = new Spool($this->path);
        $spool->append('{"a":1}');
        $spool->append('{"b":2}');

        $claimed = $spool->claim();

        self::assertCount(1, $claimed);
        self::assertFileDoesNotExist($this->path);
        self::assertMatchesRegularExpression(
            '~/megooflags-0123456789abcdef\.events\.' . getmypid() . '\.[0-9a-f]+\.sending$~',
            $claimed[0],
        );
        self::assertSame(['{"a":1}', '{"b":2}'], $spool->lines($claimed));
        self::assertSame([], $spool->claim());
        self::assertSame([], (new Spool($this->path))->claim());

        $spool->delete($claimed);
        self::assertFileDoesNotExist($claimed[0]);
        self::assertSame([], glob($this->dir . '/*') ?: []);
    }

    public function testClaimedSpoolIsNotStaleRightAway(): void
    {
        // The spool was last appended to long ago: the claimed file must not
        // keep that mtime, or another shipper takes it as abandoned at once.
        $spool = new Spool($this->path);
        $spool->append('{"a":1}');
        touch($this->path, time() - Spool::STALE_AFTER - 60);

        $claimed = $spool->claim();

        self::assertCount(1, $claimed);
        clearstatcache();
        self::assertGreaterThanOrEqual(time() - 5, (int) filemtime($claimed[0]));
        self::assertSame([], (new Spool($this->path))->claim());
        self::assertSame(['{"a":1}'], $spool->lines($claimed));
    }

    public function testClaimWaitsForAWriterHoldingTheSpoolLock(): void
    {
        // A writer opened and locked the spool just before the claim renamed
        // it; its line must be in what the claim reads.
        $spool = new Spool($this->path);
        $spool->append('{"early":1}');
        $ready = $this->dir . '/writer-ready';
        $code = '$h = fopen($argv[1], "a"); flock($h, LOCK_EX); touch($argv[2]); usleep(300000);'
            . ' fwrite($h, "{\"late\":1}\n"); fflush($h); flock($h, LOCK_UN); fclose($h);';
        $child = proc_open([PHP_BINARY, '-r', $code, $this->path, $ready], [], $pipes);
        self::assertIsResource($child);
        $deadline = microtime(true) + 5.0;
        while (!is_file($ready) && microtime(true) < $deadline) {
            usleep(10_000);
        }
        self::assertFileExists($ready);

        $claimed = $spool->claim();
        $lines = $spool->lines($claimed);

        self::assertSame(0, proc_close($child));
        self::assertSame(['{"early":1}', '{"late":1}'], $lines);
    }

    public function testClaimWithoutASpoolFindsNothing(): void
    {
        self::assertSame([], (new Spool($this->path))->claim());
    }

    public function testStaleClaimedFilesArePickedUp(): void
    {
        mkdir($this->dir, 0o700);
        $stale = $this->dir . '/megooflags-0123456789abcdef.events.999.aaaa.sending';
        $fresh = $this->dir . '/megooflags-0123456789abcdef.events.999.bbbb.sending';
        $other = $this->dir . '/megooflags-fedcba9876543210.events.999.cccc.sending';
        file_put_contents($stale, "{\"stale\":1}\n");
        file_put_contents($fresh, "{\"fresh\":1}\n");
        file_put_contents($other, "{\"other\":1}\n");
        touch($stale, time() - 61);
        touch($other, time() - 61);
        $spool = new Spool($this->path);
        $spool->append('{"new":1}');

        $claimed = $spool->claim();

        self::assertCount(2, $claimed);
        self::assertEqualsCanonicalizing(['{"new":1}', '{"stale":1}'], $spool->lines($claimed));
        self::assertFileDoesNotExist($stale);
        self::assertFileExists($fresh);
        self::assertFileExists($other);
        // Claimed again, the stale file is not stale any more.
        self::assertSame([], $spool->claim());
    }
}
