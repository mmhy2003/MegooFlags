<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Engine;

/**
 * Result is one resolution. variant, errorCode, errorMessage, ruleIndex and
 * prerequisite are null when not set.
 *
 * @internal
 */
final readonly class Result
{
    public function __construct(
        public mixed $value,
        public ?string $variant,
        public string $reason,
        public ?string $errorCode = null,
        public ?string $errorMessage = null,
        public ?int $ruleIndex = null,
        public ?string $prerequisite = null,
    ) {}

    /**
     * metadata is ['rule_index' => i] and/or ['prerequisite' => key] when set.
     *
     * @return array<string, int|string>
     */
    public function metadata(): array
    {
        $meta = [];
        if ($this->ruleIndex !== null) {
            $meta['rule_index'] = $this->ruleIndex;
        }
        if ($this->prerequisite !== null) {
            $meta['prerequisite'] = $this->prerequisite;
        }

        return $meta;
    }
}
