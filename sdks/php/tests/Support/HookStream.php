<?php

declare(strict_types=1);

namespace MegooFlags\Tests\Support;

/**
 * HookStream is a read-only stream wrapper serving $content that runs
 * $onOpen when the stream is opened. Tests use it as a bootstrap file to
 * run code at a precise point of the provider's load: after it found no
 * cache and chose the bootstrap file, before it writes anything.
 */
final class HookStream
{
    public const PROTOCOL = 'mfhook';

    public static string $content = '';

    /** @var (\Closure(): void)|null */
    public static ?\Closure $onOpen = null;

    /** @var resource|null */
    public $context;

    private int $position = 0;

    /** @param \Closure(): void $onOpen */
    public static function register(string $content, \Closure $onOpen): string
    {
        self::$content = $content;
        self::$onOpen = $onOpen;
        stream_wrapper_register(self::PROTOCOL, self::class);

        return self::PROTOCOL . '://bootstrap.json';
    }

    public static function unregister(): void
    {
        if (in_array(self::PROTOCOL, stream_get_wrappers(), true)) {
            stream_wrapper_unregister(self::PROTOCOL);
        }
        self::$onOpen = null;
    }

    public function stream_open(string $path, string $mode, int $options, ?string &$openedPath): bool
    {
        $hook = self::$onOpen;
        self::$onOpen = null;
        if ($hook !== null) {
            $hook();
        }

        return true;
    }

    public function stream_read(int $count): string
    {
        $chunk = substr(self::$content, $this->position, $count);
        $this->position += strlen($chunk);

        return $chunk;
    }

    public function stream_eof(): bool
    {
        return $this->position >= strlen(self::$content);
    }

    /** @return array<string, int> */
    public function stream_stat(): array
    {
        return ['size' => strlen(self::$content), 'mode' => 0o100600];
    }

    /** @return array<string, int> */
    public function url_stat(string $path, int $flags): array
    {
        return ['size' => strlen(self::$content), 'mode' => 0o100600];
    }
}
