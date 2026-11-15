<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Events;

use MegooFlags\Internal\Transport\Http;
use stdClass;

/**
 * Sender ships spool lines to POST /sdk/v1/events (delivery.md §7). It
 * sums the counts per flag, variant and hour, keeps one exposure per
 * context key, flag, variant and UTC hour, and sends batches of at most
 * 1000 items per list:
 *
 * - a body over 1 000 000 bytes, or a 413, is split in two halves
 *   (exposures first); a single item that is too large is dropped;
 * - 2xx is sent; 422 and any other 4xx drop the batch;
 * - 401 or 403 stop at once: everything is dropped and fatal is set;
 * - 429, 5xx or a network error stop: what was not sent comes back as
 *   one spool line in remainder, with retryAfter (0 when the server gave
 *   none) for the caller's backoff.
 *
 * The optional dedup callback (the daemon's LRU) returns true when an
 * exposure key was seen before and remembers it. A remainder line is
 * marked "deduplicated" so that its exposures skip the callback when they
 * are shipped again.
 *
 * @internal
 *
 * @phpstan-type Exposure array{key: string, e: stdClass}
 * @phpstan-type Count array{string, ?string, int, int}
 * @phpstan-type Batch array{exposures: list<Exposure>, counts: list<Count>}
 * @phpstan-type ShipResult array{dropped: int, retryAfter: ?float, remainder: ?string, fatal: ?string}
 */
final class Sender
{
    public const MAX_ITEMS = 1000;

    public const MAX_BODY_BYTES = 1_000_000;

    public const MAX_COUNT = 1_000_000_000;

    private const TIMEOUT = 10.0;

    private int $dropped = 0;

    private ?string $fatal = null;

    private ?float $retryAfter = null;

    /**
     * @param (\Closure(string): bool)|null $dedup
     */
    public function __construct(private readonly Http $http, private readonly ?\Closure $dedup = null) {}

    /**
     * ship sends the items of $lines. Malformed lines and items are skipped.
     *
     * @param list<string> $lines
     * @return ShipResult
     */
    public function ship(array $lines): array
    {
        $this->reset();
        [$exposures, $counts] = $this->aggregate($lines);

        $remainder = null;
        while ($exposures !== [] || $counts !== []) {
            $batch = [
                'exposures' => array_slice($exposures, 0, self::MAX_ITEMS),
                'counts' => array_slice($counts, 0, self::MAX_ITEMS),
            ];
            $exposures = array_slice($exposures, self::MAX_ITEMS);
            $counts = array_slice($counts, self::MAX_ITEMS);
            $unsent = $this->send($batch);
            if ($this->fatal !== null) {
                break;
            }
            if ($this->retryAfter !== null) {
                $remainder = self::remainderLine(
                    array_merge($unsent['exposures'], $exposures),
                    array_merge($unsent['counts'], $counts),
                );
                break;
            }
        }

        return ['dropped' => $this->dropped, 'retryAfter' => $this->retryAfter, 'remainder' => $remainder, 'fatal' => $this->fatal];
    }

    private function reset(): void
    {
        $this->dropped = 0;
        $this->fatal = null;
        $this->retryAfter = null;
    }

    /**
     * @param list<string> $lines
     * @return array{list<Exposure>, list<Count>}
     */
    private function aggregate(array $lines): array
    {
        /** @var array<string, Count> $sums */
        $sums = [];
        $exposures = [];
        $local = [];
        foreach ($lines as $text) {
            $line = json_decode($text, false);
            if (!$line instanceof stdClass) {
                continue;
            }
            foreach (is_array($line->counts ?? null) ? $line->counts : [] as $count) {
                if (
                    !is_array($count) || count($count) !== 4
                    || !is_string($count[0]) || !(is_string($count[1]) || $count[1] === null)
                    || !is_int($count[2]) || !is_int($count[3]) || $count[3] <= 0
                ) {
                    continue;
                }
                $key = Recorder::encode([$count[0], $count[1], $count[2]]);
                if (isset($sums[$key])) {
                    $sums[$key][3] += $count[3];
                } else {
                    $sums[$key] = [$count[0], $count[1], $count[2], $count[3]];
                }
            }
            $deduplicated = ($line->deduplicated ?? false) === true;
            foreach (is_array($line->exposures ?? null) ? $line->exposures : [] as $e) {
                if (
                    !$e instanceof stdClass
                    || !is_string($e->flag_key ?? null) || !is_string($e->variant ?? null)
                    || !is_string($e->reason ?? null) || !is_string($e->timestamp ?? null)
                    || !is_string($e->context_key ?? null)
                ) {
                    continue;
                }
                // The first 13 characters of the timestamp are the UTC hour.
                $key = Recorder::encode([$e->context_key, $e->flag_key, $e->variant, substr($e->timestamp, 0, 13)]);
                if (isset($local[$key])) {
                    continue;
                }
                $local[$key] = true;
                if (!$deduplicated && $this->dedup !== null && ($this->dedup)($key)) {
                    continue;
                }
                $contextKey = $e->context_key;
                unset($e->context_key);
                $exposures[] = ['key' => $contextKey, 'e' => $e];
            }
        }

        $counts = [];
        foreach ($sums as [$flag, $variant, $hour, $n]) {
            for (; $n > 0; $n -= self::MAX_COUNT) {
                $counts[] = [$flag, $variant, $hour, min($n, self::MAX_COUNT)];
            }
        }

        return [$exposures, $counts];
    }

    /**
     * send posts $batch, splitting it when it is too large, and returns
     * the part that was not sent (empty unless retryAfter is set).
     *
     * @param Batch $batch
     * @return Batch
     */
    private function send(array $batch): array
    {
        $none = ['exposures' => [], 'counts' => []];
        $body = self::body($batch);
        if (strlen($body) > self::MAX_BODY_BYTES) {
            return $this->split($batch);
        }
        $response = $this->http->post('/sdk/v1/events', $body, self::TIMEOUT);
        $fatal = $response->fatal();
        if ($response->isSuccess()) {
            return $none;
        }
        if ($fatal !== null) {
            $this->fatal = $fatal;

            return $none;
        }
        if ($response->status === 413) {
            return $this->split($batch);
        }
        if ($response->status === 429) {
            $this->retryAfter = $response->retryAfterSeconds();

            return $batch;
        }
        if ($response->status === 0 || $response->status >= 500) {
            $this->retryAfter = 0.0;

            return $batch;
        }
        $this->dropped += count($batch['exposures']) + count($batch['counts']);

        return $none;
    }

    /**
     * @param Batch $batch
     * @return Batch
     */
    private function split(array $batch): array
    {
        $n = count($batch['exposures']) + count($batch['counts']);
        if ($n <= 1) {
            $this->dropped += $n;

            return ['exposures' => [], 'counts' => []];
        }
        $half = intdiv($n, 2);
        $ne = min($half, count($batch['exposures']));
        $nc = $half - $ne;
        $first = ['exposures' => array_slice($batch['exposures'], 0, $ne), 'counts' => array_slice($batch['counts'], 0, $nc)];
        $second = ['exposures' => array_slice($batch['exposures'], $ne), 'counts' => array_slice($batch['counts'], $nc)];
        $unsent = $this->send($first);
        if ($this->fatal !== null) {
            return ['exposures' => [], 'counts' => []];
        }
        if ($this->retryAfter !== null) {
            return [
                'exposures' => array_merge($unsent['exposures'], $second['exposures']),
                'counts' => array_merge($unsent['counts'], $second['counts']),
            ];
        }

        return $this->send($second);
    }

    /**
     * body is the B7 events body: both lists always present, the hour in
     * RFC 3339 and a null variant for unserved counts.
     *
     * @param Batch $batch
     */
    private static function body(array $batch): string
    {
        $counts = [];
        foreach ($batch['counts'] as [$flag, $variant, $hour, $n]) {
            $counts[] = ['flag_key' => $flag, 'variant' => $variant, 'hour' => gmdate('Y-m-d\TH:i:s\Z', $hour), 'count' => $n];
        }

        return Recorder::encode([
            'exposures' => array_map(static fn(array $e): stdClass => $e['e'], $batch['exposures']),
            'counts' => $counts,
        ]);
    }

    /**
     * @param list<Exposure> $exposures
     * @param list<Count> $counts
     */
    private static function remainderLine(array $exposures, array $counts): ?string
    {
        if ($exposures === [] && $counts === []) {
            return null;
        }
        $items = [];
        foreach ($exposures as $e) {
            // The spool order: the body fields, context_key, then context.
            $fields = get_object_vars($e['e']);
            $item = array_diff_key($fields, ['context' => true]);
            $item['context_key'] = $e['key'];
            if (array_key_exists('context', $fields)) {
                $item['context'] = $fields['context'];
            }
            $items[] = (object) $item;
        }

        return Recorder::encode(['counts' => $counts, 'exposures' => $items, 'deduplicated' => true]);
    }
}
