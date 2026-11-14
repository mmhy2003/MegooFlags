using System;
using System.Collections.Generic;

namespace MegooFlags.Internal.Events;

/// <summary>
/// One flag evaluation as the provider records it. Served is false when the caller's default was returned (an
/// error, or no ruleset yet). ContextKey is "" when the context has no string key. Context is the evaluation
/// context as a JSON tree; it is sent only with SendContext. A struct, so recording allocates nothing in the queue.
/// </summary>
internal readonly record struct Evaluation(
    string FlagKey,
    string Variant,
    bool Served,
    string Reason,
    string ContextKey,
    IDictionary<string, object?>? Context,
    DateTimeOffset At);
