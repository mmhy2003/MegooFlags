using System;
using System.Collections.Generic;

namespace MegooFlags.Internal.Engine;

/// <summary>
/// A parsed SemVer 2.0.0 version. Build metadata is dropped, as it has no precedence. The core parts are digit
/// strings without leading zeros, so they compare by (length, text) without overflow.
/// </summary>
internal sealed class Semver
{
    private readonly string[] _core;
    private readonly string[]? _pre; // null when the version has no prerelease

    private Semver(string[] core, string[]? pre)
    {
        _core = core;
        _pre = pre;
    }

    /// <summary>
    /// Parse parses a strict SemVer 2.0.0 string, or returns null: ASCII only, no "v" prefix, no surrounding
    /// whitespace, no leading zeros in numeric parts.
    /// </summary>
    public static Semver? Parse(string s)
    {
        var rest = s;
        var plus = rest.IndexOf('+');
        if (plus >= 0)
        {
            foreach (var id in rest[(plus + 1)..].Split('.'))
            {
                if (!IsIdent(id))
                {
                    return null;
                }
            }
            rest = rest[..plus];
        }
        string[]? pre = null;
        var dash = rest.IndexOf('-');
        if (dash >= 0)
        {
            pre = rest[(dash + 1)..].Split('.');
            foreach (var id in pre)
            {
                if (!IsIdent(id) || (IsDigits(id) && !IsNumericPart(id)))
                {
                    return null;
                }
            }
            rest = rest[..dash];
        }
        var core = rest.Split('.');
        if (core.Length != 3)
        {
            return null;
        }
        foreach (var p in core)
        {
            if (!IsNumericPart(p))
            {
                return null;
            }
        }
        return new Semver(core, pre);
    }

    /// <summary>Compare returns -1, 0 or 1 by SemVer 2.0.0 precedence.</summary>
    public static int Compare(Semver a, Semver b)
    {
        for (var i = 0; i < 3; i++)
        {
            var c = CompareDigits(a._core[i], b._core[i]);
            if (c != 0)
            {
                return c;
            }
        }
        if (a._pre == null)
        {
            return b._pre == null ? 0 : 1;
        }
        if (b._pre == null)
        {
            return -1;
        }
        var n = Math.Min(a._pre.Length, b._pre.Length);
        for (var i = 0; i < n; i++)
        {
            var x = a._pre[i];
            var y = b._pre[i];
            var xn = IsDigits(x);
            var yn = IsDigits(y);
            int c;
            if (xn && yn)
            {
                c = CompareDigits(x, y);
            }
            else if (xn)
            {
                c = -1;
            }
            else if (yn)
            {
                c = 1;
            }
            else
            {
                c = Math.Sign(string.CompareOrdinal(x, y));
            }
            if (c != 0)
            {
                return c;
            }
        }
        return a._pre.Length.CompareTo(b._pre.Length);
    }

    /// <summary>CompareDigits compares two digit strings without leading zeros numerically.</summary>
    private static int CompareDigits(string a, string b)
    {
        if (a.Length != b.Length)
        {
            return a.Length.CompareTo(b.Length);
        }
        return Math.Sign(string.CompareOrdinal(a, b));
    }

    /// <summary>IsNumericPart reports whether s is "0" or [1-9][0-9]*.</summary>
    private static bool IsNumericPart(string s) => IsDigits(s) && (s == "0" || s[0] != '0');

    /// <summary>IsDigits reports whether s is a non-empty run of ASCII digits.</summary>
    private static bool IsDigits(string s)
    {
        if (s.Length == 0)
        {
            return false;
        }
        foreach (var c in s)
        {
            if (!char.IsAsciiDigit(c))
            {
                return false;
            }
        }
        return true;
    }

    /// <summary>IsIdent reports whether s is a non-empty run of [0-9A-Za-z-].</summary>
    private static bool IsIdent(string s)
    {
        if (s.Length == 0)
        {
            return false;
        }
        foreach (var c in s)
        {
            if (!char.IsAsciiLetterOrDigit(c) && c != '-')
            {
                return false;
            }
        }
        return true;
    }
}
