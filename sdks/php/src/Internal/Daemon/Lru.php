<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Daemon;

/**
 * Lru remembers up to $capacity keys, forgetting the least recently seen
 * one first. The daemon keeps one across shipments so that an exposure is
 * sent once per context key, flag, variant and UTC hour.
 *
 * @internal
 */
final class Lru
{
    /** @var array<string, true> keys in order of use, oldest first */
    private array $keys = [];

    public function __construct(private readonly int $capacity) {}

    /**
     * seen returns true when $key was seen before (and makes it the most
     * recent), else remembers it and returns false.
     */
    public function seen(string $key): bool
    {
        // A prefix keeps numeric strings from becoming integer array keys.
        $slot = 'k' . $key;
        if (isset($this->keys[$slot])) {
            unset($this->keys[$slot]);
            $this->keys[$slot] = true;

            return true;
        }
        if (count($this->keys) >= $this->capacity) {
            unset($this->keys[array_key_first($this->keys)]);
        }
        $this->keys[$slot] = true;

        return false;
    }

    public function count(): int
    {
        return count($this->keys);
    }
}
