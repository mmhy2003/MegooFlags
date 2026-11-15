<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Daemon;

/**
 * SseParser reads a text/event-stream fed in chunks of any size.
 *
 * Lines end with LF or CRLF and are split on the LF byte, so a multi-byte
 * character split across chunks stays whole; a line of any length is
 * accepted. Every line, comments included, calls onLine. A line starting
 * with ':' is a comment. "field:value" loses one optional space after the
 * colon; a line without a colon is a field with an empty value. Fields:
 * event, data (several lines are joined with LF), id (ignored when it
 * contains NUL) and retry (digits only, milliseconds, calls onRetry);
 * other fields are ignored. An empty line dispatches the event (name
 * "message" by default) when a data line was seen, then resets. An
 * unterminated last event is never dispatched.
 *
 * An exception thrown by a callback propagates out of feed().
 *
 * @internal
 */
final class SseParser
{
    private string $buffer = '';

    private string $name = '';

    private string $id = '';

    /** @var list<string> */
    private array $data = [];

    /**
     * @param \Closure(): void $onLine
     * @param \Closure(int): void $onRetry
     * @param \Closure(string, string, string): void $onEvent receives the name, data and id
     */
    public function __construct(
        private readonly \Closure $onLine,
        private readonly \Closure $onRetry,
        private readonly \Closure $onEvent,
    ) {}

    public function feed(string $chunk): void
    {
        // Only the new bytes are searched for LF: the buffer has none.
        $start = 0;
        $from = strlen($this->buffer);
        $this->buffer .= $chunk;
        while (($lf = strpos($this->buffer, "\n", $from)) !== false) {
            $line = substr($this->buffer, $start, $lf - $start);
            $start = $from = $lf + 1;
            $this->line($line);
        }
        if ($start > 0) {
            $this->buffer = substr($this->buffer, $start);
        }
    }

    private function line(string $line): void
    {
        if (str_ends_with($line, "\r")) {
            $line = substr($line, 0, -1);
        }
        ($this->onLine)();
        if ($line === '') {
            $this->dispatch();

            return;
        }
        if ($line[0] === ':') {
            return;
        }
        $colon = strpos($line, ':');
        if ($colon === false) {
            $field = $line;
            $value = '';
        } else {
            $field = substr($line, 0, $colon);
            $value = substr($line, $colon + 1);
            if (str_starts_with($value, ' ')) {
                $value = substr($value, 1);
            }
        }
        match ($field) {
            'event' => $this->name = $value,
            'data' => $this->data[] = $value,
            'id' => str_contains($value, "\0") ? null : $this->id = $value,
            'retry' => preg_match('/^[0-9]+$/D', $value) === 1 ? ($this->onRetry)((int) $value) : null,
            default => null,
        };
    }

    private function dispatch(): void
    {
        $data = $this->data;
        $name = $this->name === '' ? 'message' : $this->name;
        $id = $this->id;
        $this->name = '';
        $this->id = '';
        $this->data = [];
        if ($data !== []) {
            ($this->onEvent)($name, implode("\n", $data), $id);
        }
    }
}
