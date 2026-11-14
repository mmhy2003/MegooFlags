package engine

import (
	"regexp"
	"regexp/syntax"
	"strings"
	"unicode/utf8"
)

// Regex is a pattern compiled with the portable semantics of evaluation.md:
// Python's re module with re.ASCII.
type Regex struct {
	re     *regexp.Regexp
	dollar bool
}

const (
	asciiSpace       = `\t\n\v\f\r `
	asciiNonSpaceSet = `\x00-\x08\x0E-\x1F\x21-\x{10FFFF}`
)

// CompilePortable compiles pattern so that Go matches like Python re.ASCII:
//   - under (?i) ASCII letters fold only to their ASCII pair, so k does not
//     match U+212A (KELVIN SIGN) and s does not match U+017F (LONG S);
//   - \s and \S include \v;
//   - $ (without (?m)) also matches before a final "\n" (see MatchString).
//
// \d, \w and \b are already ASCII in Go. A pattern Go cannot parse, such as a
// lookaround or a backreference, returns an error.
//
// Known residual: Go folds a case-insensitive pattern while it parses and keeps
// only the minimum fold rune of each literal, so the original case of a
// non-ASCII letter is lost. Under (?i) a non-ASCII letter therefore still folds
// Unicode: (?i)é matches É in Go but not in Python.
func CompilePortable(pattern string) (*Regex, error) {
	tree, err := syntax.Parse(rewriteSpaceEscapes(pattern), syntax.Perl)
	if err != nil {
		return nil, err
	}
	tree = foldASCII(tree)
	re, err := regexp.Compile(tree.String())
	if err != nil {
		return nil, err
	}
	return &Regex{re: re, dollar: hasDollar(tree)}, nil
}

// MatchString reports whether s contains a match. Like Python, a $ also
// matches before a final newline.
func (r *Regex) MatchString(s string) bool {
	if r.re.MatchString(s) {
		return true
	}
	if r.dollar && strings.HasSuffix(s, "\n") {
		return r.re.MatchString(s[:len(s)-1])
	}
	return false
}

// rewriteSpaceEscapes replaces \s and \S with explicit ASCII sets that
// include \v. Every other escape is copied unchanged.
func rewriteSpaceEscapes(p string) string {
	var b strings.Builder
	inClass := false
	for i := 0; i < len(p); i++ {
		c := p[i]
		switch {
		case c == '\\' && i+1 < len(p):
			next := p[i+1]
			switch {
			case next == 's' && inClass:
				b.WriteString(asciiSpace)
			case next == 's':
				b.WriteString("[" + asciiSpace + "]")
			case next == 'S' && inClass:
				b.WriteString(asciiNonSpaceSet)
			case next == 'S':
				b.WriteString("[^" + asciiSpace + "]")
			default:
				b.WriteByte(c)
				b.WriteByte(next)
			}
			i++
		case inClass && c == '[' && strings.HasPrefix(p[i:], "[:"):
			// A POSIX class such as [:alpha:] is copied whole, so its ']'
			// does not close the enclosing class.
			if end := strings.Index(p[i+2:], ":]"); end >= 0 {
				n := i + 2 + end + 2
				b.WriteString(p[i:n])
				i = n - 1
			} else {
				b.WriteByte(c)
			}
		case inClass && c == ']':
			inClass = false
			b.WriteByte(c)
		case !inClass && c == '[':
			inClass = true
			b.WriteByte(c)
			// A ']' right after '[' or '[^' is a literal.
			if i+1 < len(p) && p[i+1] == '^' {
				b.WriteByte('^')
				i++
			}
			if i+1 < len(p) && p[i+1] == ']' {
				b.WriteByte(']')
				i++
			}
		default:
			b.WriteByte(c)
		}
	}
	return b.String()
}

// foldASCII rewrites every case-insensitive node so that ASCII letters fold
// only to their ASCII pair.
func foldASCII(re *syntax.Regexp) *syntax.Regexp {
	for i, sub := range re.Sub {
		re.Sub[i] = foldASCII(sub)
	}
	if re.Flags&syntax.FoldCase == 0 {
		return re
	}
	switch re.Op {
	case syntax.OpLiteral:
		return foldLiteral(re)
	case syntax.OpCharClass:
		re.Rune = removeRunes(re.Rune, 0x017F, 0x212A)
		re.Flags &^= syntax.FoldCase
	}
	return re
}

// foldLiteral splits a case-insensitive literal into one node per rune: an
// ASCII letter becomes the class [Xx], any other ASCII rune an exact literal.
// A non-ASCII rune keeps its Unicode folding (the known residual): its
// original case is gone, and an explicit class such as [éÉ] also parses to a
// case-insensitive literal.
func foldLiteral(re *syntax.Regexp) *syntax.Regexp {
	flags := re.Flags &^ syntax.FoldCase
	nodes := make([]*syntax.Regexp, 0, len(re.Rune))
	for _, r := range re.Rune {
		n := &syntax.Regexp{Op: syntax.OpLiteral, Flags: flags, Rune: []rune{r}}
		switch {
		case r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z':
			upper, lower := r&^0x20, r|0x20
			n.Op = syntax.OpCharClass
			n.Rune = []rune{upper, upper, lower, lower}
		case r >= utf8.RuneSelf:
			n.Flags |= syntax.FoldCase
		}
		nodes = append(nodes, n)
	}
	if len(nodes) == 1 {
		return nodes[0]
	}
	return &syntax.Regexp{Op: syntax.OpConcat, Flags: flags, Sub: nodes}
}

// removeRunes removes each of the given runes from a sorted list of
// [lo, hi] ranges, splitting a range when needed.
func removeRunes(ranges []rune, remove ...rune) []rune {
	for _, x := range remove {
		out := make([]rune, 0, len(ranges)+2)
		for i := 0; i+1 < len(ranges); i += 2 {
			lo, hi := ranges[i], ranges[i+1]
			if x < lo || x > hi {
				out = append(out, lo, hi)
				continue
			}
			if lo < x {
				out = append(out, lo, x-1)
			}
			if x < hi {
				out = append(out, x+1, hi)
			}
		}
		ranges = out
	}
	return ranges
}

// hasDollar reports whether the tree contains a $ that matches only at the
// end of the text.
func hasDollar(re *syntax.Regexp) bool {
	if re.Op == syntax.OpEndText && re.Flags&syntax.WasDollar != 0 {
		return true
	}
	for _, sub := range re.Sub {
		if hasDollar(sub) {
			return true
		}
	}
	return false
}
