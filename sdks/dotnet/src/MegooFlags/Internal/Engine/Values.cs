namespace MegooFlags.Internal.Engine;

/// <summary>
/// JSON values as the engine sees them. An integer keeps its exact value, so two integers compare exactly beyond
/// 2^53; any other pair of numbers compares as doubles. A boolean is never a number.
/// </summary>
internal static class Values
{
    /// <summary>IsNumber reports whether v is a number. A boolean is never a number.</summary>
    public static bool IsNumber(object? v) => IsInteger(v) || v is double or float;

    /// <summary>IsScalar reports whether v is a string, a boolean or a number.</summary>
    public static bool IsScalar(object? v) => v is string or bool || IsNumber(v);

    /// <summary>
    /// JsonEqual is JSON scalar equality: a boolean only equals a boolean, numbers compare numerically, a string
    /// equals a string. Anything else, null included, is never equal.
    /// </summary>
    public static bool JsonEqual(object? a, object? b)
    {
        if (a is bool || b is bool)
        {
            return a is bool x && b is bool y && x == y;
        }
        if (IsNumber(a))
        {
            return IsNumber(b) && CompareNumbers(a!, b!) == 0;
        }
        return a is string s && b is string t && s == t;
    }

    /// <summary>
    /// CompareNumbers returns -1, 0 or 1. Both arguments must be numbers (see <see cref="IsNumber"/>). Two integers
    /// compare exactly; otherwise as doubles.
    /// </summary>
    public static int CompareNumbers(object a, object b)
    {
        if (IsInteger(a) && IsInteger(b))
        {
            return ToLong(a).CompareTo(ToLong(b));
        }
        var x = ToDouble(a);
        var y = ToDouble(b);
        return x < y ? -1 : x > y ? 1 : 0;
    }

    internal static bool IsInteger(object? v) => v is long or int or short or sbyte or byte or ushort or uint;

    /// <summary>ToLong is an integer (see <see cref="IsInteger"/>) as a long.</summary>
    internal static long ToLong(object v) => v switch
    {
        long l => l,
        int i => i,
        short s => s,
        sbyte sb => sb,
        byte b => b,
        ushort us => us,
        uint ui => ui,
        _ => throw new System.ArgumentException("not an integer", nameof(v)),
    };

    /// <summary>ToDouble is a number (see <see cref="IsNumber"/>) as a double.</summary>
    internal static double ToDouble(object v) => v switch
    {
        double d => d,
        float f => f,
        _ => ToLong(v),
    };
}
