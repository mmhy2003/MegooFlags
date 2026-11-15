<?php

declare(strict_types=1);

namespace MegooFlags\Internal\Engine;

use RuntimeException;

/**
 * ParseException is a ruleset that does not follow spec/ruleset.schema.json.
 * The message is "<json path>: <reason>".
 *
 * @internal
 */
final class ParseException extends RuntimeException {}
