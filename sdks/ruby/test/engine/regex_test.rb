# frozen_string_literal: true

require_relative "../test_helper"
require "megooflags/internal/engine/regex"

class RegexTest < Minitest::Test
  Regex = MegooFlags::Internal::Engine::Regex

  LF = "\n"
  CR = "\r"
  VT = "\v"

  # Python re with re.ASCII semantics.
  VECTORS = [
    ["b", "abc", true],
    ["b", "xyz", false],
    ["^ab$", "ab", true],
    ["^ab$", "abc", false],
    ["^ab$", "xab", false],
    ["^ab$", "ab#{LF}", true],
    ["^ab$", "ab#{LF}#{LF}", false],
    ["^ab$", "ab#{CR}", false],
    ['\d', "a3", true],
    ['\d', "٣", false],
    ["(?i)k", "K", true],
    ["(?i)k", "K", false],
    ["(?i)^[s]$", "S", true],
    ["(?i)^[s]$", "ſ", false],
    ["(?i)^[j-l]$", "K", true],
    ["(?i)^[j-l]$", "K", false],
    ["(?i)é", "é", true],
    ["(?i)é", "É", false],
    ["(?i)[^a]", "A", false],
    ["(?i)[^a]", "b", true],
    ["^(?i:ab)c$", "ABc", true],
    ["^(?i:ab)c$", "ABC", false],
    ["(?im)^B$", "a#{LF}b", true],
    ['^\s$', VT, true],
    ['^\s$', " ", true],
    ['^\s$', " ", false],
    ['^\s$', "\u0085", false],
    ['^\s$', " ", false],
    ['^\S$', VT, false],
    ['^\S$', " ", true],
    ['^[\s]$', VT, true],
    ['^[^\d]$', "x", true],
    ['^[^\d]$', "5", false],
    ['^[\w-]$', "-", true],
    ['^[\w-]$', "a", true],
    ['^[\w-]$', "é", false],
    ['^\w+$', "a_1", true],
    ['^\w+$', "é", false],
    ["a\\bé", "aé", true],
    ["a\\Bé", "aé", false],
    ['\bfoo\b', "a foo b", true],
    ['\bfoo\b', "afoo", false],
    ["^.$", CR, true],
    ["^.$", LF, false],
    ["^.$", " ", true],
    ["^.$", "\u{1F600}", true],
    ["^..$", "\u{1F600}", false],
    ["^[\u{1F600}a]$", "\u{1F600}", true],
    ["^[\u{1F600}a]$", "a", true],
    ["^a.b$", "a#{LF}b", false],
    ["(?s)^a.b$", "a#{LF}b", true],
    ["^b$", "a#{LF}b#{LF}c", false],
    ["(?m)^b$", "a#{LF}b#{LF}c", true],
    ["(?m)a$", "a#{LF}b", true],
    ["(?m)a$", "a#{CR}#{LF}b", false],
    ["(?m)^$", "a#{LF}#{LF}b", true],
    ['(?m)\Ab', "b#{LF}a", true],
    ['(?m)\Ab', "a#{LF}b", false],
    ["^$", "", true],
    ["^$", LF, true],
    ["", "", true],
    ["", "x", true],
    ["^(?P<x>ab)+c$", "ababc", true],
    ["^(?:a|b|)$", "", true],
    ["^(?:a|b|)$", "c", false],
    ["^a{2,3}$", "aa", true],
    ["^a{2,3}$", "aaaa", false],
    ["^a{2,}$", "aaaaa", true],
    ["^a*?$", "aaa", true],
    ["^a{2}?$", "", false],
    ["^a{2}?$", "a", false],
    ["^a{2}?$", "aa", true],
    ["^a{2}?b$", "b", false],
    ["^a{2}?b$", "aab", true],
    ["^a{1}?$", "", false],
    ["^a{0}?$", "", true],
    ["^a{2,3}?$", "aaa", true],
    ["^a{2,3}?$", "a", false],
    ["^a{2,}?$", "aaaa", true],
    ["^a{2,}?$", "a", false],
    ["x{", "x{", true],
    ["x{a}", "x{a}", true],
    ['^\x41\t\.$', "A\t.", true],
    ["^[a[]$", "[", true],
    ["^[a[]$", "a", true],
    ["^[a[]$", "b", false],
    ["^[a&&b]$", "&", true],
    ['^[a\-z]$', "-", true],
    ['^[a\-z]$', "b", false],
    ["^[a-]$", "-", true],
    ["^[]a]$", "]", true],
    ["^[^]a]$", "b", true],
    ["^[^]a]$", "]", false],
    ["^[a-c-e]$", "-", true],
    ["^[a-c-e]$", "d", false],
    ['^a\|b$', "a|b", true]
  ].freeze

  REFUSED = [
    "(?=a)", "(?!a)", "(?<=a)b", "(?<!a)b", '(a)\1', "(?P<n>a)(?P=n)", "(?P<n>a)(?P<n>b)", "(?>a)",
    "(?(1)a|b)", "(?<n>a)", "(?#c)a", "a*+", "a++", "a?+", "a**", "a{2}{3}", "*a", "(*a)", "a|*", "^*",
    '\b+', "a{,2}", "a{3,2}", "a{1001}", '\z', '\Z', '\pL', '\x{41}', '\N{LATIN CAPITAL LETTER A}', '\e',
    '\q', '\0', "\\", "(?x)a", "(?U)a", "(?a)a", "a(?i)b", "(?-i:a)", "[[:alpha:]]", '[a-\d]', '[\w-a]',
    "[z-a]", '[\b]', "[]", "[a", "(", ")", "(a"
  ].freeze

  W = '[\u{30}-\u{39}\u{41}-\u{5A}\u{5F}\u{61}-\u{7A}]'

  EMITTED = [
    ["^a$", '\A\u{61}(?=\u{A}?\z)'],
    ["(?m)^a$", '(?:\A|(?<=\u{A}))\u{61}(?=\u{A}|\z)'],
    ['\bx', "(?:(?<=#{W})(?!#{W})|(?<!#{W})(?=#{W}))\\u{78}"],
    ["(?i)[a-c]", '[\u{41}-\u{43}\u{61}-\u{63}]'],
    ["a{2}?", '\u{61}{2}'],
    ["a{2,3}?", '\u{61}{2,3}?'],
    ["a{2,}?", '\u{61}{2,}?'],
    ["a??", '\u{61}??']
  ].freeze

  def test_vectors
    VECTORS.each do |pattern, subject, match|
      assert_equal match, compiled(pattern).test(subject), "#{pattern.inspect} against #{subject.inspect}"
    end
  end

  def test_refused
    REFUSED.each do |pattern|
      matcher, reason = Regex.compile(pattern)

      assert_nil matcher, pattern.inspect
      assert_kind_of String, reason, pattern.inspect
      refute_empty reason, pattern.inspect
    end
  end

  def test_emitter
    EMITTED.each do |pattern, source|
      assert_equal source, compiled(pattern).source, pattern.inspect
    end
  end

  def test_never_emits_line_anchors
    VECTORS.each do |pattern, _, _|
      source = compiled(pattern).source

      refute_includes source, "^", pattern.inspect
      refute_includes source, "$", pattern.inspect
    end
  end

  def test_sets_leave_out_surrogates
    assert_equal '[\u{0}-\u{9}\u{B}-\u{D7FF}\u{E000}-\u{10FFFF}]', compiled(".").source
    assert compiled("^.$").test("\u{E000}")
  end

  def test_repeats_an_empty_set
    matcher = compiled('^[^\s\S]*$')

    assert matcher.test("")
    refute matcher.test("a")
  end

  def test_invalid_utf8_subject_never_matches
    refute compiled("a").test((+"a\xFF").force_encoding(Encoding::UTF_8))
    refute compiled("").test((+"\xC3").force_encoding(Encoding::UTF_8))
  end

  def test_binary_subject_with_invalid_bytes_never_matches
    refute compiled("a").test((+"a\xFF").force_encoding(Encoding::BINARY))
  end

  def test_subject_in_another_encoding_is_transcoded
    assert compiled("^é$").test("é".encode(Encoding::ISO_8859_1))
    assert compiled("^a$").test((+"a").force_encoding(Encoding::BINARY))
  end

  private

  def compiled(pattern)
    matcher, reason = Regex.compile(pattern)
    raise "#{pattern.inspect}: #{reason}" unless matcher

    matcher
  end
end
