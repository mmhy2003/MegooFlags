<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Support;

use RuntimeException;

/**
 * FakeServer is a child process running the PHP built-in server with
 * router.php. Tests set the responses per route with respond() and read
 * the requests the server saw with requests().
 *
 * @phpstan-type FakeResponse array{status?: int, headers?: array<string, string>, body?: string,
 *     delay?: float|int, chunks?: list<array{float|int, string}>}
 * @phpstan-type FakeRequest array{method: string, path: string, query: string,
 *     headers: array<string, string>, body: string}
 */
final class FakeServer
{
    /** @var resource */
    private $process;

    /** @var array<string, list<array<string, mixed>>> */
    private array $scenario = [];

    private function __construct(private readonly string $dir, private readonly int $port)
    {
        $this->writeScenario();
        $env = getenv();
        $env['MEGOOFLAGS_FAKE_DIR'] = $dir;
        $process = proc_open(
            [PHP_BINARY, '-S', '127.0.0.1:' . $port, __DIR__ . '/router.php'],
            [0 => ['file', '/dev/null', 'r'], 1 => ['file', $dir . '/server.log', 'a'], 2 => ['file', $dir . '/server.log', 'a']],
            $pipes,
            $dir,
            $env,
        );
        if ($process === false) {
            throw new RuntimeException('cannot start the fake server');
        }
        $this->process = $process;
    }

    public static function start(): self
    {
        $dir = sys_get_temp_dir() . '/megooflags-fake-' . bin2hex(random_bytes(6));
        mkdir($dir, 0o700);
        for ($attempt = 0; $attempt < 5; $attempt++) {
            $server = new self($dir, self::freePort());
            if ($server->waitUntilUp()) {
                return $server;
            }
            $server->stop(false);
        }

        throw new RuntimeException('the fake server did not start: ' . (string) @file_get_contents($dir . '/server.log'));
    }

    public function url(): string
    {
        return 'http://127.0.0.1:' . $this->port;
    }

    /**
     * respond sets the responses of a route, served in order; the last one
     * repeats.
     *
     * @param FakeResponse ...$responses
     */
    public function respond(string $method, string $path, array ...$responses): void
    {
        $this->scenario[$method . ' ' . $path] = array_values($responses);
        $this->writeScenario();
    }

    /** @return list<FakeRequest> */
    public function requests(): array
    {
        $file = $this->dir . '/requests.jsonl';
        if (!is_file($file)) {
            return [];
        }
        $requests = [];
        foreach (file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [] as $line) {
            /** @var FakeRequest $request */
            $request = json_decode($line, true, 512, JSON_THROW_ON_ERROR);
            $requests[] = $request;
        }

        return $requests;
    }

    public function stop(bool $removeDir = true): void
    {
        if (is_resource($this->process)) {
            proc_terminate($this->process, 9);
            proc_close($this->process);
        }
        if ($removeDir && is_dir($this->dir)) {
            foreach (glob($this->dir . '/*') ?: [] as $file) {
                unlink($file);
            }
            rmdir($this->dir);
        }
    }

    public function __destruct()
    {
        $this->stop();
    }

    private function writeScenario(): void
    {
        $json = json_encode((object) $this->scenario, JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION | JSON_THROW_ON_ERROR);
        file_put_contents($this->dir . '/scenario.json.tmp', $json);
        rename($this->dir . '/scenario.json.tmp', $this->dir . '/scenario.json');
    }

    private static function freePort(): int
    {
        $socket = stream_socket_server('tcp://127.0.0.1:0', $errno, $errstr);
        if ($socket === false) {
            throw new RuntimeException('no free port: ' . $errstr);
        }
        $name = (string) stream_socket_get_name($socket, false);
        fclose($socket);

        return (int) substr($name, (int) strrpos($name, ':') + 1);
    }

    private function waitUntilUp(): bool
    {
        $deadline = microtime(true) + 5.0;
        while (microtime(true) < $deadline) {
            $status = proc_get_status($this->process);
            if (!$status['running']) {
                return false;
            }
            $conn = @stream_socket_client('tcp://127.0.0.1:' . $this->port, $errno, $errstr, 0.2);
            if ($conn !== false) {
                fclose($conn);

                return true;
            }
            usleep(20_000);
        }

        return false;
    }
}
