package io.github.mmhy2003.megooflags.internal.engine;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.regex.PatternSyntaxException;

/**
 * Portable regex translator: the subset common to RE2 and Python re, with Python re.ASCII
 * semantics, rewritten into explicit java.util.regex constructs so that no host default matters.
 * Three stages: parse (recursive descent over code points), an AST whose sets are sorted merged
 * code point ranges, and the emitter. The Pattern is compiled with no flags.
 */
public final class PortableRegex {
    private static final System.Logger LOGGER = System.getLogger("io.github.mmhy2003.megooflags");

    private final Pattern pattern;

    private PortableRegex(Pattern pattern) {
        this.pattern = pattern;
    }

    /**
     * compile compiles pattern with the portable semantics of evaluation.md (Python re with
     * re.ASCII), or throws RegexSyntaxException with the reason when the pattern is outside the
     * portable subset.
     */
    public static PortableRegex compile(String pattern) throws RegexSyntaxException {
        StringBuilder source = new StringBuilder();
        new Parser(pattern).parse().emit(source);
        try {
            return new PortableRegex(Pattern.compile(source.toString()));
        } catch (PatternSyntaxException e) {
            throw new RegexSyntaxException("host regex error: " + e.getDescription());
        }
    }

    /**
     * test is an unanchored search of subject. java.util.regex recurses per repetition of some
     * groups, so a long subject can overflow the stack; that search is logged and does not match.
     */
    public boolean test(String subject) {
        try {
            return pattern.matcher(subject).find();
        } catch (StackOverflowError e) {
            LOGGER.log(
                    System.Logger.Level.WARNING,
                    "megooflags: regex search overflowed the stack on a {0}-char subject; treating it as no match",
                    subject.length());
            return false;
        }
    }

    /** source is the emitted java.util.regex source. */
    public String source() {
        return pattern.pattern();
    }

    /** flags are the Pattern flags, always 0. */
    public int flags() {
        return pattern.flags();
    }

    // ---- Sets: sorted, merged, inclusive code point ranges ----

    private static final int MAX_CP = 0x10FFFF;
    private static final int MAX_REPEAT = 1000;

    private static List<int[]> normalize(List<int[]> ranges) {
        List<int[]> sorted = new ArrayList<>(ranges);
        sorted.sort((a, b) -> Integer.compare(a[0], b[0]));
        List<int[]> out = new ArrayList<>();
        for (int[] r : sorted) {
            int[] last = out.isEmpty() ? null : out.get(out.size() - 1);
            if (last != null && r[0] <= last[1] + 1) {
                if (r[1] > last[1]) {
                    last[1] = r[1];
                }
            } else {
                out.add(new int[] {r[0], r[1]});
            }
        }
        return out;
    }

    private static List<int[]> complement(List<int[]> ranges) {
        List<int[]> out = new ArrayList<>();
        int next = 0;
        for (int[] r : ranges) {
            if (r[0] > next) {
                out.add(new int[] {next, r[0] - 1});
            }
            next = r[1] + 1;
        }
        if (next <= MAX_CP) {
            out.add(new int[] {next, MAX_CP});
        }
        return out;
    }

    /** foldAscii adds the other case of every ASCII letter in the set. */
    private static List<int[]> foldAscii(List<int[]> ranges) {
        List<int[]> all = new ArrayList<>(ranges);
        for (int[] r : ranges) {
            addShifted(all, r, 0x41, 0x5A, 0x20);
            addShifted(all, r, 0x61, 0x7A, -0x20);
        }
        return normalize(all);
    }

    private static void addShifted(List<int[]> out, int[] r, int a, int z, int shift) {
        int lo = Math.max(r[0], a);
        int hi = Math.min(r[1], z);
        if (lo <= hi) {
            out.add(new int[] {lo + shift, hi + shift});
        }
    }

    private static List<int[]> ranges(int... bounds) {
        List<int[]> out = new ArrayList<>();
        for (int k = 0; k < bounds.length; k += 2) {
            out.add(new int[] {bounds[k], bounds[k + 1]});
        }
        return Collections.unmodifiableList(out);
    }

    private static final List<int[]> DIGIT = ranges(0x30, 0x39);
    private static final List<int[]> WORD = ranges(0x30, 0x39, 0x41, 0x5A, 0x5F, 0x5F, 0x61, 0x7A);
    private static final List<int[]> SPACE = ranges(0x09, 0x0D, 0x20, 0x20);

    /** shorthand returns the set of \d \D \w \W \s \S, or null for any other letter. */
    private static List<int[]> shorthand(int c) {
        switch (c) {
            case 'd':
                return DIGIT;
            case 'D':
                return complement(DIGIT);
            case 'w':
                return WORD;
            case 'W':
                return complement(WORD);
            case 's':
                return SPACE;
            case 'S':
                return complement(SPACE);
            default:
                return null;
        }
    }

    /** controlEscape returns the code point of \t \n \r \f \v \a, or -1. */
    private static int controlEscape(int c) {
        switch (c) {
            case 't':
                return 0x09;
            case 'n':
                return 0x0A;
            case 'r':
                return 0x0D;
            case 'f':
                return 0x0C;
            case 'v':
                return 0x0B;
            case 'a':
                return 0x07;
            default:
                return -1;
        }
    }

    // ---- AST ----

    private enum AssertKind {
        START,
        END_DOLLAR,
        LINE_START,
        LINE_END,
        WORD_B,
        NOT_WORD_B
    }

    private abstract static class Node {
        abstract void emit(StringBuilder out);
    }

    private static final class SetNode extends Node {
        final List<int[]> ranges;

        SetNode(List<int[]> ranges) {
            this.ranges = ranges;
        }

        @Override
        void emit(StringBuilder out) {
            emitSet(out, ranges);
        }
    }

    private static final class ConcatNode extends Node {
        final List<Node> items;

        ConcatNode(List<Node> items) {
            this.items = items;
        }

        @Override
        void emit(StringBuilder out) {
            for (Node n : items) {
                n.emit(out);
            }
        }
    }

    private static final class AltNode extends Node {
        final List<Node> alternatives;

        AltNode(List<Node> alternatives) {
            this.alternatives = alternatives;
        }

        @Override
        void emit(StringBuilder out) {
            for (int k = 0; k < alternatives.size(); k++) {
                if (k > 0) {
                    out.append('|');
                }
                alternatives.get(k).emit(out);
            }
        }
    }

    private static final class GroupNode extends Node {
        final Node child;

        GroupNode(Node child) {
            this.child = child;
        }

        @Override
        void emit(StringBuilder out) {
            out.append("(?:");
            child.emit(out);
            out.append(')');
        }
    }

    private static final class RepeatNode extends Node {
        final Node child;
        final int min;
        /** -1 when unbounded. */
        final int max;

        final boolean lazy;

        RepeatNode(Node child, int min, int max, boolean lazy) {
            this.child = child;
            this.min = min;
            this.max = max;
            this.lazy = lazy;
        }

        @Override
        void emit(StringBuilder out) {
            // An empty set is a lookahead; wrap it so the quantifier applies to a group.
            boolean wrap = child instanceof SetNode && ((SetNode) child).ranges.isEmpty();
            if (wrap) {
                out.append("(?:");
            }
            child.emit(out);
            if (wrap) {
                out.append(')');
            }
            if (min == 0 && max == -1) {
                out.append('*');
            } else if (min == 1 && max == -1) {
                out.append('+');
            } else if (min == 0 && max == 1) {
                out.append('?');
            } else if (max == -1) {
                out.append('{').append(min).append(",}");
            } else if (min == max) {
                out.append('{').append(min).append('}');
            } else {
                out.append('{').append(min).append(',').append(max).append('}');
            }
            if (lazy) {
                out.append('?');
            }
        }
    }

    private static final class AssertNode extends Node {
        final AssertKind kind;

        AssertNode(AssertKind kind) {
            this.kind = kind;
        }

        @Override
        void emit(StringBuilder out) {
            out.append(emitAssert(kind));
        }
    }

    /** Flags i, m and s, lexically scoped. */
    private static final class Flags {
        static final Flags NONE = new Flags(false, false, false);

        final boolean i;
        final boolean m;
        final boolean s;

        Flags(boolean i, boolean m, boolean s) {
            this.i = i;
            this.m = m;
            this.s = s;
        }

        Flags with(Flags set) {
            return new Flags(i || set.i, m || set.m, s || set.s);
        }
    }

    /** A "(?" [ims]+ group head ending in ")" or ":", not yet consumed. */
    private static final class FlagGroup {
        final Flags flags;
        final int terminator;
        final int end;

        FlagGroup(Flags flags, int terminator, int end) {
            this.flags = flags;
            this.terminator = terminator;
            this.end = end;
        }
    }

    /** A quantifier read at the current position, not yet consumed. max is -1 when unbounded. */
    private static final class Quantifier {
        final int min;
        final int max;
        final int end;

        Quantifier(int min, int max, int end) {
            this.min = min;
            this.max = max;
            this.end = end;
        }
    }

    // ---- Stage 1: parse ----

    private static boolean isAsciiAlnum(int c) {
        return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
    }

    private static boolean isDigit(int c) {
        return c >= '0' && c <= '9';
    }

    private static int hexValue(int c) {
        if (c >= '0' && c <= '9') {
            return c - '0';
        }
        if (c >= 'A' && c <= 'F') {
            return c - 'A' + 10;
        }
        if (c >= 'a' && c <= 'f') {
            return c - 'a' + 10;
        }
        return -1;
    }

    private static boolean isGroupName(String name) {
        if (name.isEmpty()) {
            return false;
        }
        for (int k = 0; k < name.length(); k++) {
            char c = name.charAt(k);
            boolean ok = c == '_' || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (k > 0 && isDigit(c));
            if (!ok) {
                return false;
            }
        }
        return true;
    }

    private static Node literal(int c, Flags flags) {
        List<int[]> set = ranges(c, c);
        return new SetNode(flags.i ? foldAscii(set) : set);
    }

    private static final class Parser {
        private final int[] cps;
        private int pos;
        private final Set<String> names = new HashSet<>();

        Parser(String pattern) {
            int[] buf = new int[pattern.length()];
            int n = 0;
            for (int k = 0; k < pattern.length(); ) {
                int c = pattern.codePointAt(k);
                buf[n++] = c;
                k += Character.charCount(c);
            }
            cps = Arrays.copyOf(buf, n);
        }

        Node parse() throws RegexSyntaxException {
            Flags flags = Flags.NONE;
            for (; ; ) {
                FlagGroup global = flagGroupAt(pos);
                if (global == null || global.terminator != ')') {
                    break;
                }
                flags = flags.with(global.flags);
                pos = global.end;
            }
            Node node = alternation(flags);
            if (pos < cps.length) {
                // Only an unmatched ")" stops the top-level alternation.
                throw fail("unbalanced parenthesis");
            }
            return node;
        }

        private RegexSyntaxException fail(String reason) {
            return new RegexSyntaxException(reason + " at offset " + pos);
        }

        /** at returns the code point at index k, or -1 past the end. */
        private int at(int k) {
            return k < cps.length ? cps[k] : -1;
        }

        private int peek(int offset) {
            return at(pos + offset);
        }

        private FlagGroup flagGroupAt(int start) {
            if (at(start) != '(' || at(start + 1) != '?') {
                return null;
            }
            boolean i = false;
            boolean m = false;
            boolean s = false;
            int j = start + 2;
            for (; ; j++) {
                int c = at(j);
                if (c == 'i') {
                    i = true;
                } else if (c == 'm') {
                    m = true;
                } else if (c == 's') {
                    s = true;
                } else {
                    break;
                }
            }
            if (j == start + 2) {
                return null;
            }
            int t = at(j);
            if (t != ')' && t != ':') {
                return null;
            }
            return new FlagGroup(new Flags(i, m, s), t, j + 1);
        }

        private Node alternation(Flags flags) throws RegexSyntaxException {
            List<Node> alternatives = new ArrayList<>();
            alternatives.add(concat(flags));
            while (peek(0) == '|') {
                pos++;
                alternatives.add(concat(flags));
            }
            return alternatives.size() == 1 ? alternatives.get(0) : new AltNode(alternatives);
        }

        private Node concat(Flags flags) throws RegexSyntaxException {
            List<Node> items = new ArrayList<>();
            for (; ; ) {
                int c = peek(0);
                if (c == -1 || c == '|' || c == ')') {
                    break;
                }
                if (quantifierAt() != null) {
                    throw fail("nothing to repeat");
                }
                Node atom = atom(flags);
                Quantifier q = quantifierAt();
                if (q == null) {
                    items.add(atom);
                    continue;
                }
                if (atom instanceof AssertNode) {
                    throw fail("nothing to repeat");
                }
                pos = q.end;
                boolean lazy = false;
                if (peek(0) == '?') {
                    lazy = true;
                    pos++;
                }
                if (quantifierAt() != null) {
                    throw fail("multiple repeat");
                }
                items.add(new RepeatNode(atom, q.min, q.max, lazy));
            }
            return items.size() == 1 ? items.get(0) : new ConcatNode(items);
        }

        /**
         * quantifierAt reads a quantifier at the current position without consuming it. A "{"
         * that is not {n}, {n,} or {n,m} is a literal, except {,…} which is refused.
         */
        private Quantifier quantifierAt() throws RegexSyntaxException {
            int c = peek(0);
            if (c == '*') {
                return new Quantifier(0, -1, pos + 1);
            }
            if (c == '+') {
                return new Quantifier(1, -1, pos + 1);
            }
            if (c == '?') {
                return new Quantifier(0, 1, pos + 1);
            }
            if (c != '{') {
                return null;
            }
            int j = pos + 1;
            int nStart = j;
            while (isDigit(at(j))) {
                j++;
            }
            String n = digits(nStart, j);
            if (n.isEmpty()) {
                if (at(j) == ',') {
                    j++;
                    while (isDigit(at(j))) {
                        j++;
                    }
                    if (at(j) == '}') {
                        throw fail("missing repeat minimum");
                    }
                }
                return null;
            }
            String m = n;
            if (at(j) == ',') {
                j++;
                int mStart = j;
                while (isDigit(at(j))) {
                    j++;
                }
                m = digits(mStart, j);
            }
            if (at(j) != '}') {
                return null;
            }
            int min = repeatCount(n);
            int max = m.isEmpty() ? -1 : repeatCount(m);
            if (max != -1 && min > max) {
                throw fail("repeat minimum over maximum");
            }
            return new Quantifier(min, max, j + 1);
        }

        private String digits(int from, int to) {
            return new String(cps, from, to - from);
        }

        /** repeatCount parses a digit string, refusing counts over 1000 without overflow. */
        private int repeatCount(String digits) throws RegexSyntaxException {
            int n = 0;
            for (int k = 0; k < digits.length(); k++) {
                n = n * 10 + (digits.charAt(k) - '0');
                if (n > MAX_REPEAT) {
                    throw fail("repeat count over 1000");
                }
            }
            return n;
        }

        private Node atom(Flags flags) throws RegexSyntaxException {
            int c = peek(0);
            switch (c) {
                case '(':
                    return group(flags);
                case '[':
                    return charClass(flags);
                case '.':
                    pos++;
                    return new SetNode(flags.s ? ranges(0, MAX_CP) : complement(ranges(0x0A, 0x0A)));
                case '^':
                    pos++;
                    return new AssertNode(flags.m ? AssertKind.LINE_START : AssertKind.START);
                case '$':
                    pos++;
                    return new AssertNode(flags.m ? AssertKind.LINE_END : AssertKind.END_DOLLAR);
                case '\\':
                    return escape(flags);
                default:
                    pos++;
                    return literal(c, flags);
            }
        }

        private Node group(Flags flags) throws RegexSyntaxException {
            if (peek(1) != '?') {
                pos++;
                return groupBody(flags);
            }
            FlagGroup scoped = flagGroupAt(pos);
            if (scoped != null) {
                if (scoped.terminator == ')') {
                    throw fail("global flags not at the start");
                }
                pos = scoped.end;
                return groupBody(flags.with(scoped.flags));
            }
            if (peek(2) == ':') {
                pos += 3;
                return groupBody(flags);
            }
            if (peek(2) == 'P' && peek(3) == '<') {
                pos += 4;
                int start = pos;
                for (; ; ) {
                    int n = peek(0);
                    if (n == -1) {
                        throw fail("unterminated group name");
                    }
                    if (n == '>') {
                        break;
                    }
                    pos++;
                }
                String name = new String(cps, start, pos - start);
                if (!isGroupName(name)) {
                    throw fail("bad group name");
                }
                if (!names.add(name)) {
                    throw fail("duplicate group name");
                }
                pos++;
                return groupBody(flags);
            }
            throw fail("unsupported group syntax");
        }

        private Node groupBody(Flags flags) throws RegexSyntaxException {
            Node child = alternation(flags);
            if (peek(0) != ')') {
                throw fail("missing )");
            }
            pos++;
            return new GroupNode(child);
        }

        private Node escape(Flags flags) throws RegexSyntaxException {
            int e = peek(1);
            if (e == -1) {
                throw fail("trailing backslash");
            }
            List<int[]> set = shorthand(e);
            if (set != null) {
                pos += 2;
                return new SetNode(set);
            }
            if (e == 'A' || e == 'b' || e == 'B') {
                pos += 2;
                return new AssertNode(
                        e == 'A' ? AssertKind.START : e == 'b' ? AssertKind.WORD_B : AssertKind.NOT_WORD_B);
            }
            return literal(singleEscape(), flags);
        }

        /**
         * singleEscape reads an escape that stands for one code point: \t \n \r \f \v \a, \xHH, or
         * a backslash before ASCII punctuation.
         */
        private int singleEscape() throws RegexSyntaxException {
            int e = peek(1);
            if (e == -1) {
                throw fail("trailing backslash");
            }
            int control = controlEscape(e);
            if (control != -1) {
                pos += 2;
                return control;
            }
            if (e == 'x') {
                int h = hexValue(peek(2));
                int l = hexValue(peek(3));
                if (h < 0 || l < 0) {
                    throw fail("\\x needs two hex digits");
                }
                pos += 4;
                return h * 16 + l;
            }
            if (e < 0x80 && !isAsciiAlnum(e)) {
                pos += 2;
                return e;
            }
            throw fail("unsupported escape \\" + new String(Character.toChars(e)));
        }

        private Node charClass(Flags flags) throws RegexSyntaxException {
            pos++;
            boolean negated = false;
            if (peek(0) == '^') {
                negated = true;
                pos++;
            }
            List<int[]> members = new ArrayList<>();
            boolean first = true;
            for (; ; ) {
                int c = peek(0);
                if (c == -1) {
                    throw fail("unterminated character class");
                }
                if (c == ']' && !first) {
                    break;
                }
                first = false;
                List<int[]> set = classShorthand();
                if (set != null) {
                    if (isRangeDash()) {
                        throw fail("bad character range");
                    }
                    members.addAll(set);
                    continue;
                }
                int lo = classCodePoint();
                if (isRangeDash()) {
                    pos++;
                    if (classShorthand() != null) {
                        throw fail("bad character range");
                    }
                    int hi = classCodePoint();
                    if (lo > hi) {
                        throw fail("bad character range");
                    }
                    members.add(new int[] {lo, hi});
                    continue;
                }
                members.add(new int[] {lo, lo});
            }
            pos++;
            List<int[]> set = normalize(members);
            if (flags.i) {
                set = foldAscii(set);
            }
            if (negated) {
                set = complement(set);
            }
            return new SetNode(set);
        }

        /** isRangeDash reports whether a "-" at pos is followed by a range end. */
        private boolean isRangeDash() {
            int next = peek(1);
            return peek(0) == '-' && next != -1 && next != ']';
        }

        /** classShorthand consumes \d \D \w \W \s \S inside a class, or returns null. */
        private List<int[]> classShorthand() throws RegexSyntaxException {
            if (peek(0) == '[' && peek(1) == ':') {
                throw fail("POSIX class");
            }
            if (peek(0) != '\\') {
                return null;
            }
            List<int[]> set = shorthand(peek(1));
            if (set != null) {
                pos += 2;
            }
            return set;
        }

        /** classCodePoint consumes a literal or a single code point escape inside a class. */
        private int classCodePoint() throws RegexSyntaxException {
            int c = peek(0);
            if (c != '\\') {
                pos++;
                return c;
            }
            return singleEscape();
        }
    }

    // ---- Stage 3: emit (Java column) ----

    private static void appendCodePoint(StringBuilder out, int c) {
        out.append("\\x{")
                .append(Integer.toHexString(c).toUpperCase(java.util.Locale.ROOT))
                .append('}');
    }

    private static void emitSet(StringBuilder out, List<int[]> ranges) {
        if (ranges.isEmpty()) {
            out.append("(?!)");
            return;
        }
        if (ranges.size() == 1 && ranges.get(0)[0] == ranges.get(0)[1]) {
            appendCodePoint(out, ranges.get(0)[0]);
            return;
        }
        out.append('[');
        for (int[] r : ranges) {
            appendCodePoint(out, r[0]);
            if (r[1] != r[0]) {
                out.append('-');
                appendCodePoint(out, r[1]);
            }
        }
        out.append(']');
    }

    private static String setSource(List<int[]> ranges) {
        StringBuilder out = new StringBuilder();
        emitSet(out, ranges);
        return out.toString();
    }

    private static final String START = "\\A";
    private static final String END = "\\z";
    private static final String LF = setSource(ranges(0x0A, 0x0A));
    private static final String W = setSource(WORD);

    private static String emitAssert(AssertKind kind) {
        switch (kind) {
            case START:
                return START;
            case END_DOLLAR:
                return "(?=" + LF + "?" + END + ")";
            case LINE_START:
                return "(?:" + START + "|(?<=" + LF + "))";
            case LINE_END:
                return "(?=" + LF + "|" + END + ")";
            case WORD_B:
                return "(?:(?<=" + W + ")(?!" + W + ")|(?<!" + W + ")(?=" + W + "))";
            case NOT_WORD_B:
                return "(?:(?<=" + W + ")(?=" + W + ")|(?<!" + W + ")(?!" + W + "))";
            default:
                throw new AssertionError(kind);
        }
    }
}
