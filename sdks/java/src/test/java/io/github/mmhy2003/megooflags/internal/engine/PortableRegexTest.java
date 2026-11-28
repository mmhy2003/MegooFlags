package io.github.mmhy2003.megooflags.internal.engine;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

class PortableRegexTest {
    private static final String LF = "\n";
    private static final String CR = "\r";
    private static final String VT = "\u000b";
    private static final String GRIN = new String(Character.toChars(0x1F600));
    private static final String W = "[\\x{30}-\\x{39}\\x{41}-\\x{5A}\\x{5F}\\x{61}-\\x{7A}]";

    // Python re with re.ASCII semantics.
    static Stream<Arguments> vectors() {
        return Stream.of(
                Arguments.of("b", "abc", true),
                Arguments.of("b", "xyz", false),
                Arguments.of("^ab$", "ab", true),
                Arguments.of("^ab$", "abc", false),
                Arguments.of("^ab$", "xab", false),
                Arguments.of("^ab$", "ab" + LF, true),
                Arguments.of("^ab$", "ab" + LF + LF, false),
                Arguments.of("^ab$", "ab" + CR, false),
                Arguments.of("\\d", "a3", true),
                Arguments.of("\\d", "٣", false),
                Arguments.of("(?i)k", "K", true),
                Arguments.of("(?i)k", "K", false),
                Arguments.of("(?i)^[s]$", "S", true),
                Arguments.of("(?i)^[s]$", "ſ", false),
                Arguments.of("(?i)^[j-l]$", "K", true),
                Arguments.of("(?i)^[j-l]$", "K", false),
                Arguments.of("(?i)é", "é", true),
                Arguments.of("(?i)é", "É", false),
                Arguments.of("(?i)[^a]", "A", false),
                Arguments.of("(?i)[^a]", "b", true),
                Arguments.of("^(?i:ab)c$", "ABc", true),
                Arguments.of("^(?i:ab)c$", "ABC", false),
                Arguments.of("(?im)^B$", "a" + LF + "b", true),
                Arguments.of("^\\s$", VT, true),
                Arguments.of("^\\s$", " ", true),
                Arguments.of("^\\s$", " ", false),
                Arguments.of("^\\s$", "\u0085", false),
                Arguments.of("^\\s$", " ", false),
                Arguments.of("^\\S$", VT, false),
                Arguments.of("^\\S$", " ", true),
                Arguments.of("^[\\s]$", VT, true),
                Arguments.of("^[^\\d]$", "x", true),
                Arguments.of("^[^\\d]$", "5", false),
                Arguments.of("^[\\w-]$", "-", true),
                Arguments.of("^[\\w-]$", "a", true),
                Arguments.of("^[\\w-]$", "é", false),
                Arguments.of("^\\w+$", "a_1", true),
                Arguments.of("^\\w+$", "é", false),
                Arguments.of("a\\bé", "aé", true),
                Arguments.of("a\\Bé", "aé", false),
                Arguments.of("\\bfoo\\b", "a foo b", true),
                Arguments.of("\\bfoo\\b", "afoo", false),
                Arguments.of("^.$", CR, true),
                Arguments.of("^.$", LF, false),
                Arguments.of("^.$", " ", true),
                Arguments.of("^.$", GRIN, true),
                Arguments.of("^..$", GRIN, false),
                Arguments.of("^[" + GRIN + "a]$", GRIN, true),
                Arguments.of("^[" + GRIN + "a]$", "a", true),
                Arguments.of("^a.b$", "a" + LF + "b", false),
                Arguments.of("(?s)^a.b$", "a" + LF + "b", true),
                Arguments.of("^b$", "a" + LF + "b" + LF + "c", false),
                Arguments.of("(?m)^b$", "a" + LF + "b" + LF + "c", true),
                Arguments.of("(?m)a$", "a" + LF + "b", true),
                Arguments.of("(?m)a$", "a" + CR + LF + "b", false),
                Arguments.of("(?m)^$", "a" + LF + LF + "b", true),
                Arguments.of("(?m)\\Ab", "b" + LF + "a", true),
                Arguments.of("(?m)\\Ab", "a" + LF + "b", false),
                Arguments.of("^$", "", true),
                Arguments.of("^$", LF, true),
                Arguments.of("", "", true),
                Arguments.of("", "x", true),
                Arguments.of("^(?P<x>ab)+c$", "ababc", true),
                Arguments.of("^(?:a|b|)$", "", true),
                Arguments.of("^(?:a|b|)$", "c", false),
                Arguments.of("^a{2,3}$", "aa", true),
                Arguments.of("^a{2,3}$", "aaaa", false),
                Arguments.of("^a{2,}$", "aaaaa", true),
                Arguments.of("^a*?$", "aaa", true),
                Arguments.of("x{", "x{", true),
                Arguments.of("x{a}", "x{a}", true),
                Arguments.of("^\\x41\\t\\.$", "A\t.", true),
                Arguments.of("^[a[]$", "[", true),
                Arguments.of("^[a[]$", "a", true),
                Arguments.of("^[a[]$", "b", false),
                Arguments.of("^[a&&b]$", "&", true),
                Arguments.of("^[a\\-z]$", "-", true),
                Arguments.of("^[a\\-z]$", "b", false),
                Arguments.of("^[a-]$", "-", true),
                Arguments.of("^[]a]$", "]", true),
                Arguments.of("^[^]a]$", "b", true),
                Arguments.of("^[^]a]$", "]", false),
                Arguments.of("^[a-c-e]$", "-", true),
                Arguments.of("^[a-c-e]$", "d", false),
                Arguments.of("^a\\|b$", "a|b", true));
    }

    @ParameterizedTest
    @MethodSource("vectors")
    void matchesLikePythonAscii(String pattern, String subject, boolean match) throws RegexSyntaxException {
        assertEquals(match, PortableRegex.compile(pattern).test(subject));
    }

    static Stream<String> refused() {
        return Stream.of(
                "(?=a)",
                "(?!a)",
                "(?<=a)b",
                "(?<!a)b",
                "(a)\\1",
                "(?P<n>a)(?P=n)",
                "(?P<n>a)(?P<n>b)",
                "(?>a)",
                "(?(1)a|b)",
                "(?<n>a)",
                "(?#c)a",
                "a*+",
                "a++",
                "a?+",
                "a**",
                "a{2}{3}",
                "*a",
                "(*a)",
                "a|*",
                "^*",
                "\\b+",
                "a{,2}",
                "a{3,2}",
                "a{1001}",
                "\\z",
                "\\Z",
                "\\pL",
                "\\x{41}",
                "\\N{LATIN CAPITAL LETTER A}",
                "\\e",
                "\\q",
                "\\0",
                "\\",
                "(?x)a",
                "(?U)a",
                "(?a)a",
                "a(?i)b",
                "(?-i:a)",
                "[[:alpha:]]",
                "[a-\\d]",
                "[\\w-a]",
                "[z-a]",
                "[\\b]",
                "[]",
                "[a",
                "(",
                ")",
                "(a");
    }

    @ParameterizedTest
    @MethodSource("refused")
    void refuses(String pattern) {
        RegexSyntaxException e = assertThrows(RegexSyntaxException.class, () -> PortableRegex.compile(pattern));
        assertFalse(e.getMessage().isEmpty());
    }

    static Stream<Arguments> emitted() {
        return Stream.of(
                Arguments.of("^a$", "\\A\\x{61}(?=\\x{A}?\\z)"),
                Arguments.of("(?m)^a$", "(?:\\A|(?<=\\x{A}))\\x{61}(?=\\x{A}|\\z)"),
                Arguments.of("\\bx", "(?:(?<=" + W + ")(?!" + W + ")|(?<!" + W + ")(?=" + W + "))\\x{78}"),
                Arguments.of("(?i)[a-c]", "[\\x{41}-\\x{43}\\x{61}-\\x{63}]"));
    }

    @ParameterizedTest
    @MethodSource("emitted")
    void emitsTheJavaColumn(String pattern, String source) throws RegexSyntaxException {
        PortableRegex re = PortableRegex.compile(pattern);
        assertEquals(source, re.source());
        assertEquals(0, re.flags());
    }

    @Test
    void aStackOverflowInTheHostMatcherIsNoMatch() throws RegexSyntaxException {
        // java.util.regex recurses once per repetition of a group with an alternation, so a long
        // subject overflows the thread stack.
        PortableRegex re = PortableRegex.compile("^(a|b)*$");
        assertFalse(re.test("a".repeat(100_000)));
        assertTrue(re.test("abba"));
    }

    @Test
    void repeatsAnEmptySetWithoutAHostError() throws RegexSyntaxException {
        PortableRegex re = PortableRegex.compile("^[^\\s\\S]*$");
        assertTrue(re.test(""));
        assertFalse(re.test("a"));
    }
}
