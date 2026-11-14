package engine

import "testing"

func TestCompilePortable(t *testing.T) {
	tests := []struct {
		pattern string
		input   string
		want    bool
	}{
		// ASCII-only case folding: Go folds k/U+212A and s/U+017F, Python re.ASCII does not.
		{`(?i)k`, "K", true},
		{`(?i)k`, "k", true},
		{`(?i)k`, "K", false},
		{`(?i)s`, "ſ", false},
		{`(?i)^[a-z]$`, "K", false},
		{`(?i)^[a-z]$`, "Q", true},
		{`(?i:ab)c`, "ABc", true},
		{`(?i:ab)c`, "ABC", false},
		{`(?i)^a1$`, "A1", true},
		// An explicit class of a non-ASCII pair parses to a fold literal and must keep both cases.
		{`^[éÉ]$`, "é", true},
		{`^[éÉ]$`, "É", true},
		{`(?i)^aé$`, "Aé", true},
		// \s includes \v, as in Python.
		{`^\s$`, "\v", true},
		{`^\s$`, "\t", true},
		{`^\S$`, "\v", false},
		{`^\S$`, "x", true},
		{`^[\s]$`, "\v", true},
		{`^[^\s]$`, "\v", false},
		{`^[\S]$`, "\v", false},
		{`^[\S]$`, "x", true},
		{`\\s`, `\s`, true},
		{`\\s`, " ", false},
		// $ also matches before a final newline.
		{`^ab$`, "ab\n", true},
		{`^ab$`, "ab\n\n", false},
		{`(?m)^ab$`, "ab\ncd", true},
		{`(?m)^ab$`, "ab\n", true},
		{`b$|zz`, "ab\n", true},
		// ASCII-only classes.
		{`\d`, "٣", false},
		{`\w`, "é", false},
	}
	for _, tt := range tests {
		re, err := CompilePortable(tt.pattern)
		if err != nil {
			t.Errorf("CompilePortable(%q) error: %v", tt.pattern, err)
			continue
		}
		if got := re.MatchString(tt.input); got != tt.want {
			t.Errorf("CompilePortable(%q).MatchString(%q) = %v, want %v", tt.pattern, tt.input, got, tt.want)
		}
	}
}

func TestCompilePortableClassEdgeCases(t *testing.T) {
	tests := []struct {
		pattern string
		input   string
		want    bool
	}{
		{`^[]\s]$`, "]", true},
		{`^[]\s]$`, "\v", true},
		{`^[^]\s]$`, "\v", false},
		{`^[^]\s]$`, "x", true},
		{`^[[:alpha:]\s]$`, "\v", true},
		{`^[[:alpha:]\s]$`, "a", true},
		{`^[\]\s]$`, "\v", true},
		{`^[a\s]b$`, "\vb", true},
	}
	for _, tt := range tests {
		re, err := CompilePortable(tt.pattern)
		if err != nil {
			t.Errorf("CompilePortable(%q) error: %v", tt.pattern, err)
			continue
		}
		if got := re.MatchString(tt.input); got != tt.want {
			t.Errorf("CompilePortable(%q).MatchString(%q) = %v, want %v", tt.pattern, tt.input, got, tt.want)
		}
	}
}

func TestCompilePortableInvalid(t *testing.T) {
	for _, pattern := range []string{`(`, `(?=a)`, `a(?<=b)`, `\1`} {
		if _, err := CompilePortable(pattern); err == nil {
			t.Errorf("CompilePortable(%q) error = nil, want an error", pattern)
		}
	}
}
