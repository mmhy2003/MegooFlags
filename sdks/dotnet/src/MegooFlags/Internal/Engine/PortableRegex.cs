using System;
using System.Buffers;
using System.Collections.Generic;
using System.Diagnostics.CodeAnalysis;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace MegooFlags.Internal.Engine;

/// <summary>
/// Portable regex translator: the subset common to RE2 and Python re, with Python re.ASCII semantics, rewritten
/// into explicit .NET constructs so that no host default matters. Three stages: parse (recursive descent over code
/// points), an AST whose sets are sorted merged code point ranges, and the emitter. .NET matches UTF-16 code units,
/// so every set is expanded into surrogate pair classes: a pair is one character and a lone surrogate is one
/// character. The Regex is created with RegexOptions.CultureInvariant only.
/// </summary>
internal sealed class PortableRegex
{
    private const RegexOptions HostOptions = RegexOptions.CultureInvariant;

    private readonly Regex _regex;

    private PortableRegex(Regex regex)
    {
        _regex = regex;
    }

    /// <summary>Source is the emitted .NET regex source.</summary>
    public string Source => _regex.ToString();

    /// <summary>Options are the Regex options, always RegexOptions.CultureInvariant.</summary>
    public RegexOptions Options => _regex.Options;

    /// <summary>
    /// TryCompile compiles pattern with the portable semantics of evaluation.md (Python re with re.ASCII), or
    /// returns false with the reason when the pattern is outside the portable subset.
    /// </summary>
    public static bool TryCompile(
        string pattern,
        [NotNullWhen(true)] out PortableRegex? regex,
        [NotNullWhen(false)] out string? error)
    {
        regex = null;
        string source;
        try
        {
            var sb = new StringBuilder();
            new Parser(pattern).Parse().Emit(sb);
            source = sb.ToString();
        }
        catch (SyntaxException e)
        {
            error = e.Message;
            return false;
        }
        try
        {
            regex = new PortableRegex(new Regex(source, HostOptions));
        }
        catch (ArgumentException e)
        {
            error = "host regex error: " + e.Message;
            return false;
        }
        error = null;
        return true;
    }

    /// <summary>Test is an unanchored search of subject.</summary>
    public bool Test(string subject) => _regex.IsMatch(subject);

    private sealed class SyntaxException : Exception
    {
        public SyntaxException(string message) : base(message)
        {
        }
    }

    // ---- Sets: sorted, merged, inclusive code point ranges ----

    private readonly record struct CpRange(int Lo, int Hi);

    private const int MaxCp = 0x10FFFF;
    private const int MaxRepeat = 1000;

    private static List<CpRange> Normalize(List<CpRange> ranges)
    {
        var sorted = new List<CpRange>(ranges);
        sorted.Sort((a, b) => a.Lo.CompareTo(b.Lo));
        var result = new List<CpRange>();
        foreach (var r in sorted)
        {
            if (result.Count > 0 && r.Lo <= result[^1].Hi + 1)
            {
                if (r.Hi > result[^1].Hi)
                {
                    result[^1] = new CpRange(result[^1].Lo, r.Hi);
                }
            }
            else
            {
                result.Add(r);
            }
        }
        return result;
    }

    private static List<CpRange> Complement(List<CpRange> ranges)
    {
        var result = new List<CpRange>();
        var next = 0;
        foreach (var r in ranges)
        {
            if (r.Lo > next)
            {
                result.Add(new CpRange(next, r.Lo - 1));
            }
            next = r.Hi + 1;
        }
        if (next <= MaxCp)
        {
            result.Add(new CpRange(next, MaxCp));
        }
        return result;
    }

    /// <summary>FoldAscii adds the other case of every ASCII letter in the set.</summary>
    private static List<CpRange> FoldAscii(List<CpRange> ranges)
    {
        var all = new List<CpRange>(ranges);
        foreach (var r in ranges)
        {
            AddShifted(all, r, 0x41, 0x5A, 0x20);
            AddShifted(all, r, 0x61, 0x7A, -0x20);
        }
        return Normalize(all);
    }

    private static void AddShifted(List<CpRange> result, CpRange r, int a, int z, int shift)
    {
        var lo = Math.Max(r.Lo, a);
        var hi = Math.Min(r.Hi, z);
        if (lo <= hi)
        {
            result.Add(new CpRange(lo + shift, hi + shift));
        }
    }

    /// <summary>Intersect returns the parts of the set inside lo to hi.</summary>
    private static List<CpRange> Intersect(List<CpRange> ranges, int lo, int hi)
    {
        var result = new List<CpRange>();
        foreach (var r in ranges)
        {
            var a = Math.Max(r.Lo, lo);
            var b = Math.Min(r.Hi, hi);
            if (a <= b)
            {
                result.Add(new CpRange(a, b));
            }
        }
        return result;
    }

    private static List<CpRange> Ranges(params int[] bounds)
    {
        var result = new List<CpRange>();
        for (var k = 0; k < bounds.Length; k += 2)
        {
            result.Add(new CpRange(bounds[k], bounds[k + 1]));
        }
        return result;
    }

    private static readonly List<CpRange> _digit = Ranges(0x30, 0x39);
    private static readonly List<CpRange> _word = Ranges(0x30, 0x39, 0x41, 0x5A, 0x5F, 0x5F, 0x61, 0x7A);
    private static readonly List<CpRange> _space = Ranges(0x09, 0x0D, 0x20, 0x20);

    /// <summary>Shorthand returns the set of \d \D \w \W \s \S, or null for any other code point.</summary>
    private static List<CpRange>? Shorthand(int c) => c switch
    {
        'd' => _digit,
        'D' => Complement(_digit),
        'w' => _word,
        'W' => Complement(_word),
        's' => _space,
        'S' => Complement(_space),
        _ => null,
    };

    /// <summary>ControlEscape returns the code point of \t \n \r \f \v \a, or -1.</summary>
    private static int ControlEscape(int c) => c switch
    {
        't' => 0x09,
        'n' => 0x0A,
        'r' => 0x0D,
        'f' => 0x0C,
        'v' => 0x0B,
        'a' => 0x07,
        _ => -1,
    };

    // ---- AST ----

    private enum AssertKind
    {
        Start,
        EndDollar,
        LineStart,
        LineEnd,
        WordB,
        NotWordB,
    }

    private abstract class Node
    {
        public abstract void Emit(StringBuilder sb);
    }

    private sealed class SetNode : Node
    {
        public SetNode(List<CpRange> ranges)
        {
            Ranges = ranges;
        }

        public List<CpRange> Ranges { get; }

        public override void Emit(StringBuilder sb) => EmitSet(sb, Ranges);
    }

    private sealed class ConcatNode : Node
    {
        private readonly List<Node> _items;

        public ConcatNode(List<Node> items)
        {
            _items = items;
        }

        public override void Emit(StringBuilder sb)
        {
            foreach (var n in _items)
            {
                n.Emit(sb);
            }
        }
    }

    private sealed class AltNode : Node
    {
        private readonly List<Node> _alternatives;

        public AltNode(List<Node> alternatives)
        {
            _alternatives = alternatives;
        }

        public override void Emit(StringBuilder sb)
        {
            for (var k = 0; k < _alternatives.Count; k++)
            {
                if (k > 0)
                {
                    sb.Append('|');
                }
                _alternatives[k].Emit(sb);
            }
        }
    }

    private sealed class GroupNode : Node
    {
        private readonly Node _child;

        public GroupNode(Node child)
        {
            _child = child;
        }

        public override void Emit(StringBuilder sb)
        {
            sb.Append("(?:");
            _child.Emit(sb);
            sb.Append(')');
        }
    }

    private sealed class RepeatNode : Node
    {
        private readonly Node _child;
        private readonly int _min;
        private readonly int _max; // -1 when unbounded
        private readonly bool _lazy;

        public RepeatNode(Node child, int min, int max, bool lazy)
        {
            _child = child;
            _min = min;
            _max = max;
            _lazy = lazy;
        }

        public override void Emit(StringBuilder sb)
        {
            // An empty set is a lookahead; wrap it so the quantifier applies to a group.
            var wrap = _child is SetNode set && set.Ranges.Count == 0;
            if (wrap)
            {
                sb.Append("(?:");
            }
            _child.Emit(sb);
            if (wrap)
            {
                sb.Append(')');
            }
            var inv = CultureInfo.InvariantCulture;
            if (_min == 0 && _max == -1)
            {
                sb.Append('*');
            }
            else if (_min == 1 && _max == -1)
            {
                sb.Append('+');
            }
            else if (_min == 0 && _max == 1)
            {
                sb.Append('?');
            }
            else if (_max == -1)
            {
                sb.Append('{').Append(_min.ToString(inv)).Append(",}");
            }
            else if (_min == _max)
            {
                sb.Append('{').Append(_min.ToString(inv)).Append('}');
            }
            else
            {
                sb.Append('{').Append(_min.ToString(inv)).Append(',').Append(_max.ToString(inv)).Append('}');
            }
            if (_lazy)
            {
                sb.Append('?');
            }
        }
    }

    private sealed class AssertNode : Node
    {
        private readonly AssertKind _kind;

        public AssertNode(AssertKind kind)
        {
            _kind = kind;
        }

        public override void Emit(StringBuilder sb) => sb.Append(EmitAssert(_kind));
    }

    /// <summary>Flags i, m and s, lexically scoped.</summary>
    private readonly record struct Flags(bool I, bool M, bool S)
    {
        public Flags With(Flags set) => new(I || set.I, M || set.M, S || set.S);
    }

    /// <summary>A "(?" [ims]+ group head ending in ")" or ":", not yet consumed.</summary>
    private readonly record struct FlagGroup(Flags Flags, int Terminator, int End);

    /// <summary>A quantifier read at the current position, not yet consumed. Max is -1 when unbounded.</summary>
    private readonly record struct Quantifier(int Min, int Max, int End);

    // ---- Stage 1: parse ----

    private static bool IsAsciiAlnum(int c) => (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');

    private static bool IsDigit(int c) => c >= '0' && c <= '9';

    private static int HexValue(int c)
    {
        if (c >= '0' && c <= '9')
        {
            return c - '0';
        }
        if (c >= 'A' && c <= 'F')
        {
            return c - 'A' + 10;
        }
        if (c >= 'a' && c <= 'f')
        {
            return c - 'a' + 10;
        }
        return -1;
    }

    private static Node Literal(int c, Flags flags)
    {
        var set = Ranges(c, c);
        return new SetNode(flags.I ? FoldAscii(set) : set);
    }

    private sealed class Parser
    {
        private readonly int[] _cps;
        private readonly HashSet<string> _names = new(StringComparer.Ordinal);
        private int _pos;

        public Parser(string pattern)
        {
            // By code point; a lone surrogate stands for itself.
            var cps = new List<int>(pattern.Length);
            for (var k = 0; k < pattern.Length;)
            {
                if (Rune.DecodeFromUtf16(pattern.AsSpan(k), out var rune, out var used) == OperationStatus.Done)
                {
                    cps.Add(rune.Value);
                    k += used;
                }
                else
                {
                    cps.Add(pattern[k]);
                    k++;
                }
            }
            _cps = cps.ToArray();
        }

        public Node Parse()
        {
            var flags = default(Flags);
            while (true)
            {
                var global = FlagGroupAt(_pos);
                if (global is null || global.Value.Terminator != ')')
                {
                    break;
                }
                flags = flags.With(global.Value.Flags);
                _pos = global.Value.End;
            }
            var node = Alternation(flags);
            if (_pos < _cps.Length)
            {
                // Only an unmatched ")" stops the top-level alternation.
                throw Fail("unbalanced parenthesis");
            }
            return node;
        }

        private SyntaxException Fail(string reason) =>
            new(reason + " at offset " + _pos.ToString(CultureInfo.InvariantCulture));

        /// <summary>At returns the code point at index k, or -1 past the end.</summary>
        private int At(int k) => k < _cps.Length ? _cps[k] : -1;

        private int Peek(int offset) => At(_pos + offset);

        private FlagGroup? FlagGroupAt(int start)
        {
            if (At(start) != '(' || At(start + 1) != '?')
            {
                return null;
            }
            bool i = false, m = false, s = false;
            var j = start + 2;
            for (; ; j++)
            {
                var c = At(j);
                if (c == 'i')
                {
                    i = true;
                }
                else if (c == 'm')
                {
                    m = true;
                }
                else if (c == 's')
                {
                    s = true;
                }
                else
                {
                    break;
                }
            }
            if (j == start + 2)
            {
                return null;
            }
            var t = At(j);
            if (t != ')' && t != ':')
            {
                return null;
            }
            return new FlagGroup(new Flags(i, m, s), t, j + 1);
        }

        private Node Alternation(Flags flags)
        {
            var alternatives = new List<Node> { Concat(flags) };
            while (Peek(0) == '|')
            {
                _pos++;
                alternatives.Add(Concat(flags));
            }
            return alternatives.Count == 1 ? alternatives[0] : new AltNode(alternatives);
        }

        private Node Concat(Flags flags)
        {
            var items = new List<Node>();
            while (true)
            {
                var c = Peek(0);
                if (c == -1 || c == '|' || c == ')')
                {
                    break;
                }
                if (QuantifierAt() is not null)
                {
                    throw Fail("nothing to repeat");
                }
                var atom = Atom(flags);
                var q = QuantifierAt();
                if (q is null)
                {
                    items.Add(atom);
                    continue;
                }
                if (atom is AssertNode)
                {
                    throw Fail("nothing to repeat");
                }
                _pos = q.Value.End;
                var lazy = false;
                if (Peek(0) == '?')
                {
                    lazy = true;
                    _pos++;
                }
                if (QuantifierAt() is not null)
                {
                    throw Fail("multiple repeat");
                }
                items.Add(new RepeatNode(atom, q.Value.Min, q.Value.Max, lazy));
            }
            return items.Count == 1 ? items[0] : new ConcatNode(items);
        }

        /// <summary>
        /// QuantifierAt reads a quantifier at the current position without consuming it. A "{" that is not {n},
        /// {n,} or {n,m} is a literal, except {,…} which is refused.
        /// </summary>
        private Quantifier? QuantifierAt()
        {
            var c = Peek(0);
            if (c == '*')
            {
                return new Quantifier(0, -1, _pos + 1);
            }
            if (c == '+')
            {
                return new Quantifier(1, -1, _pos + 1);
            }
            if (c == '?')
            {
                return new Quantifier(0, 1, _pos + 1);
            }
            if (c != '{')
            {
                return null;
            }
            var j = _pos + 1;
            var nStart = j;
            while (IsDigit(At(j)))
            {
                j++;
            }
            var nEnd = j;
            if (nEnd == nStart)
            {
                if (At(j) == ',')
                {
                    j++;
                    while (IsDigit(At(j)))
                    {
                        j++;
                    }
                    if (At(j) == '}')
                    {
                        throw Fail("missing repeat minimum");
                    }
                }
                return null;
            }
            int mStart = nStart, mEnd = nEnd;
            if (At(j) == ',')
            {
                j++;
                mStart = j;
                while (IsDigit(At(j)))
                {
                    j++;
                }
                mEnd = j;
            }
            if (At(j) != '}')
            {
                return null;
            }
            var min = RepeatCount(nStart, nEnd);
            var max = mEnd == mStart ? -1 : RepeatCount(mStart, mEnd);
            if (max != -1 && min > max)
            {
                throw Fail("repeat minimum over maximum");
            }
            return new Quantifier(min, max, j + 1);
        }

        /// <summary>RepeatCount parses the digits from to to, refusing counts over 1000 without overflow.</summary>
        private int RepeatCount(int from, int to)
        {
            var n = 0;
            for (var k = from; k < to; k++)
            {
                n = (n * 10) + (_cps[k] - '0');
                if (n > MaxRepeat)
                {
                    throw Fail("repeat count over 1000");
                }
            }
            return n;
        }

        private Node Atom(Flags flags)
        {
            var c = Peek(0);
            switch (c)
            {
                case '(':
                    return Group(flags);
                case '[':
                    return CharClass(flags);
                case '.':
                    _pos++;
                    return new SetNode(flags.S ? Ranges(0, MaxCp) : Complement(Ranges(0x0A, 0x0A)));
                case '^':
                    _pos++;
                    return new AssertNode(flags.M ? AssertKind.LineStart : AssertKind.Start);
                case '$':
                    _pos++;
                    return new AssertNode(flags.M ? AssertKind.LineEnd : AssertKind.EndDollar);
                case '\\':
                    return Escape(flags);
                default:
                    _pos++;
                    return Literal(c, flags);
            }
        }

        private Node Group(Flags flags)
        {
            if (Peek(1) != '?')
            {
                _pos++;
                return GroupBody(flags);
            }
            var scoped = FlagGroupAt(_pos);
            if (scoped is not null)
            {
                if (scoped.Value.Terminator == ')')
                {
                    throw Fail("global flags not at the start");
                }
                _pos = scoped.Value.End;
                return GroupBody(flags.With(scoped.Value.Flags));
            }
            if (Peek(2) == ':')
            {
                _pos += 3;
                return GroupBody(flags);
            }
            if (Peek(2) == 'P' && Peek(3) == '<')
            {
                _pos += 4;
                var name = new StringBuilder();
                while (true)
                {
                    var n = Peek(0);
                    if (n == -1)
                    {
                        throw Fail("unterminated group name");
                    }
                    if (n == '>')
                    {
                        break;
                    }
                    var ok = n == '_' || (n >= 'A' && n <= 'Z') || (n >= 'a' && n <= 'z') || (name.Length > 0 && IsDigit(n));
                    if (!ok)
                    {
                        throw Fail("bad group name");
                    }
                    name.Append((char)n);
                    _pos++;
                }
                if (name.Length == 0)
                {
                    throw Fail("bad group name");
                }
                if (!_names.Add(name.ToString()))
                {
                    throw Fail("duplicate group name");
                }
                _pos++;
                return GroupBody(flags);
            }
            throw Fail("unsupported group syntax");
        }

        private Node GroupBody(Flags flags)
        {
            var child = Alternation(flags);
            if (Peek(0) != ')')
            {
                throw Fail("missing )");
            }
            _pos++;
            return new GroupNode(child);
        }

        private Node Escape(Flags flags)
        {
            var e = Peek(1);
            if (e == -1)
            {
                throw Fail("trailing backslash");
            }
            var set = Shorthand(e);
            if (set is not null)
            {
                _pos += 2;
                return new SetNode(set);
            }
            if (e == 'A' || e == 'b' || e == 'B')
            {
                _pos += 2;
                return new AssertNode(e == 'A' ? AssertKind.Start : e == 'b' ? AssertKind.WordB : AssertKind.NotWordB);
            }
            return Literal(SingleEscape(), flags);
        }

        /// <summary>
        /// SingleEscape reads an escape that stands for one code point: \t \n \r \f \v \a, \xHH, or a backslash
        /// before ASCII punctuation.
        /// </summary>
        private int SingleEscape()
        {
            var e = Peek(1);
            if (e == -1)
            {
                throw Fail("trailing backslash");
            }
            var control = ControlEscape(e);
            if (control != -1)
            {
                _pos += 2;
                return control;
            }
            if (e == 'x')
            {
                var h = HexValue(Peek(2));
                var l = HexValue(Peek(3));
                if (h < 0 || l < 0)
                {
                    throw Fail(@"\x needs two hex digits");
                }
                _pos += 4;
                return (h * 16) + l;
            }
            if (e < 0x80 && !IsAsciiAlnum(e))
            {
                _pos += 2;
                return e;
            }
            var shown = e < 0x80 ? ((char)e).ToString() : "U+" + e.ToString("X4", CultureInfo.InvariantCulture);
            throw Fail(@"unsupported escape \" + shown);
        }

        private Node CharClass(Flags flags)
        {
            _pos++;
            var negated = false;
            if (Peek(0) == '^')
            {
                negated = true;
                _pos++;
            }
            var members = new List<CpRange>();
            var first = true;
            while (true)
            {
                var c = Peek(0);
                if (c == -1)
                {
                    throw Fail("unterminated character class");
                }
                if (c == ']' && !first)
                {
                    break;
                }
                first = false;
                var shorthand = ClassShorthand();
                if (shorthand is not null)
                {
                    if (IsRangeDash())
                    {
                        throw Fail("bad character range");
                    }
                    members.AddRange(shorthand);
                    continue;
                }
                var lo = ClassCodePoint();
                if (IsRangeDash())
                {
                    _pos++;
                    if (ClassShorthand() is not null)
                    {
                        throw Fail("bad character range");
                    }
                    var hi = ClassCodePoint();
                    if (lo > hi)
                    {
                        throw Fail("bad character range");
                    }
                    members.Add(new CpRange(lo, hi));
                    continue;
                }
                members.Add(new CpRange(lo, lo));
            }
            _pos++;
            var set = Normalize(members);
            if (flags.I)
            {
                set = FoldAscii(set);
            }
            if (negated)
            {
                set = Complement(set);
            }
            return new SetNode(set);
        }

        /// <summary>IsRangeDash reports whether a "-" at the current position is followed by a range end.</summary>
        private bool IsRangeDash()
        {
            var next = Peek(1);
            return Peek(0) == '-' && next != -1 && next != ']';
        }

        /// <summary>ClassShorthand consumes \d \D \w \W \s \S inside a class, or returns null.</summary>
        private List<CpRange>? ClassShorthand()
        {
            if (Peek(0) == '[' && Peek(1) == ':')
            {
                throw Fail("POSIX class");
            }
            if (Peek(0) != '\\')
            {
                return null;
            }
            var set = Shorthand(Peek(1));
            if (set is not null)
            {
                _pos += 2;
            }
            return set;
        }

        /// <summary>ClassCodePoint consumes a literal or a single code point escape inside a class.</summary>
        private int ClassCodePoint()
        {
            var c = Peek(0);
            if (c != '\\')
            {
                _pos++;
                return c;
            }
            return SingleEscape();
        }
    }

    // ---- Stage 3: emit (.NET column) ----

    private const int HighFirst = 0xD800;
    private const int HighLast = 0xDBFF;
    private const int LowFirst = 0xDC00;
    private const int LowLast = 0xDFFF;

    private static void AppendUnit(StringBuilder sb, int unit) =>
        sb.Append(@"\u").Append(unit.ToString("X4", CultureInfo.InvariantCulture));

    /// <summary>AppendClass writes UTF-16 code unit ranges as a class, or as the bare escape for one unit.</summary>
    private static void AppendClass(StringBuilder sb, List<CpRange> units)
    {
        if (units.Count == 1 && units[0].Lo == units[0].Hi)
        {
            AppendUnit(sb, units[0].Lo);
            return;
        }
        sb.Append('[');
        foreach (var r in units)
        {
            AppendUnit(sb, r.Lo);
            if (r.Hi != r.Lo)
            {
                sb.Append('-');
                AppendUnit(sb, r.Hi);
            }
        }
        sb.Append(']');
    }

    private static string ClassSource(List<CpRange> units)
    {
        var sb = new StringBuilder();
        AppendClass(sb, units);
        return sb.ToString();
    }

    private static string Pair(int high, int lowFrom, int lowTo) =>
        ClassSource(Ranges(high, high)) + ClassSource(Ranges(lowFrom, lowTo));

    /// <summary>AstralParts writes each astral range as surrogate pair classes.</summary>
    private static IEnumerable<string> AstralParts(List<CpRange> astral)
    {
        foreach (var r in astral)
        {
            int ha = HighFirst + ((r.Lo - 0x10000) >> 10), la = LowFirst + ((r.Lo - 0x10000) & 0x3FF);
            int hb = HighFirst + ((r.Hi - 0x10000) >> 10), lb = LowFirst + ((r.Hi - 0x10000) & 0x3FF);
            if (ha == hb)
            {
                yield return Pair(ha, la, lb);
                continue;
            }
            var fullFrom = ha;
            var fullTo = hb;
            if (la != LowFirst)
            {
                yield return Pair(ha, la, LowLast);
                fullFrom = ha + 1;
            }
            if (lb != LowLast)
            {
                yield return Pair(hb, LowFirst, lb);
                fullTo = hb - 1;
            }
            if (fullFrom <= fullTo)
            {
                yield return ClassSource(Ranges(fullFrom, fullTo)) + ClassSource(Ranges(LowFirst, LowLast));
            }
        }
    }

    /// <summary>
    /// EmitSet writes a set as (?:A|B|H|L) without its empty parts: A the astral ranges as surrogate pairs, B the
    /// BMP ranges outside the surrogates, H the lone high surrogates and L the lone low surrogates in the set.
    /// A set made of B alone is written as that class.
    /// </summary>
    private static void EmitSet(StringBuilder sb, List<CpRange> ranges)
    {
        var parts = new List<string>(AstralParts(Intersect(ranges, 0x10000, MaxCp)));
        var bmp = Intersect(ranges, 0, HighFirst - 1);
        bmp.AddRange(Intersect(ranges, LowLast + 1, 0xFFFF));
        var bmpPart = bmp.Count > 0 ? ClassSource(bmp) : null;
        if (bmpPart is not null)
        {
            parts.Add(bmpPart);
        }
        var highs = Intersect(ranges, HighFirst, HighLast);
        if (highs.Count > 0)
        {
            parts.Add(ClassSource(highs) + "(?!" + ClassSource(Ranges(LowFirst, LowLast)) + ")");
        }
        var lows = Intersect(ranges, LowFirst, LowLast);
        if (lows.Count > 0)
        {
            parts.Add("(?<!" + ClassSource(Ranges(HighFirst, HighLast)) + ")" + ClassSource(lows));
        }
        if (parts.Count == 0)
        {
            sb.Append("(?!)");
        }
        else if (parts.Count == 1 && bmpPart is not null)
        {
            sb.Append(bmpPart);
        }
        else
        {
            sb.Append("(?:").AppendJoin('|', parts).Append(')');
        }
    }

    private const string StartSource = @"\A";
    private const string EndSource = @"\z";
    private static readonly string _lf = ClassSource(Ranges(0x0A, 0x0A));
    private static readonly string _w = ClassSource(_word);

    private static string EmitAssert(AssertKind kind) => kind switch
    {
        AssertKind.Start => StartSource,
        AssertKind.EndDollar => "(?=" + _lf + "?" + EndSource + ")",
        AssertKind.LineStart => "(?:" + StartSource + "|(?<=" + _lf + "))",
        AssertKind.LineEnd => "(?=" + _lf + "|" + EndSource + ")",
        AssertKind.WordB => "(?:(?<=" + _w + ")(?!" + _w + ")|(?<!" + _w + ")(?=" + _w + "))",
        AssertKind.NotWordB => "(?:(?<=" + _w + ")(?=" + _w + ")|(?<!" + _w + ")(?!" + _w + "))",
        _ => throw new ArgumentOutOfRangeException(nameof(kind)),
    };
}
