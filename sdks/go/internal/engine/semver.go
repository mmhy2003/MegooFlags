package engine

import "strings"

// Semver is a parsed SemVer 2.0.0 version. Build metadata is dropped, as it
// has no precedence. The core parts are digit strings without leading zeros,
// so they compare by (length, bytes) without overflow.
type Semver struct {
	core [3]string
	pre  []preID
}

// preID is one prerelease identifier.
type preID struct {
	numeric bool
	s       string
}

// ParseSemver parses a strict SemVer 2.0.0 string: ASCII only, no "v"
// prefix, no surrounding whitespace, no leading zeros in numeric parts.
func ParseSemver(s string) (Semver, bool) {
	var v Semver
	rest := s
	if i := strings.IndexByte(rest, '+'); i >= 0 {
		for _, id := range strings.Split(rest[i+1:], ".") {
			if !isIdent(id) {
				return Semver{}, false
			}
		}
		rest = rest[:i]
	}
	if i := strings.IndexByte(rest, '-'); i >= 0 {
		for _, id := range strings.Split(rest[i+1:], ".") {
			if !isIdent(id) {
				return Semver{}, false
			}
			numeric := isDigits(id)
			if numeric && !isNumericPart(id) {
				return Semver{}, false
			}
			v.pre = append(v.pre, preID{numeric: numeric, s: id})
		}
		rest = rest[:i]
	}
	parts := strings.Split(rest, ".")
	if len(parts) != 3 {
		return Semver{}, false
	}
	for i, p := range parts {
		if !isNumericPart(p) {
			return Semver{}, false
		}
		v.core[i] = p
	}
	return v, true
}

// CompareSemver returns -1, 0 or 1 by SemVer 2.0.0 precedence.
func CompareSemver(a, b Semver) int {
	for i := range a.core {
		if c := compareDigits(a.core[i], b.core[i]); c != 0 {
			return c
		}
	}
	switch {
	case a.pre == nil && b.pre == nil:
		return 0
	case a.pre == nil:
		return 1
	case b.pre == nil:
		return -1
	}
	for i := 0; i < len(a.pre) && i < len(b.pre); i++ {
		x, y := a.pre[i], b.pre[i]
		var c int
		switch {
		case x.numeric && y.numeric:
			c = compareDigits(x.s, y.s)
		case x.numeric:
			c = -1
		case y.numeric:
			c = 1
		default:
			c = strings.Compare(x.s, y.s)
		}
		if c != 0 {
			return c
		}
	}
	switch {
	case len(a.pre) < len(b.pre):
		return -1
	case len(a.pre) > len(b.pre):
		return 1
	}
	return 0
}

// compareDigits compares two digit strings without leading zeros numerically.
func compareDigits(a, b string) int {
	switch {
	case len(a) < len(b):
		return -1
	case len(a) > len(b):
		return 1
	}
	return strings.Compare(a, b)
}

// isNumericPart reports whether s is "0" or [1-9][0-9]*.
func isNumericPart(s string) bool {
	return isDigits(s) && (s == "0" || s[0] != '0')
}

// isDigits reports whether s is a non-empty run of ASCII digits.
func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
}

// isIdent reports whether s is a non-empty run of [0-9A-Za-z-].
func isIdent(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c == '-') {
			return false
		}
	}
	return true
}
