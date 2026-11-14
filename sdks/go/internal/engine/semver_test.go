package engine

import "testing"

func TestParseSemver(t *testing.T) {
	valid := []string{
		"0.0.0",
		"1.2.3",
		"10.20.30",
		"99999999999999999999.0.0",
		"1.0.0-alpha",
		"1.0.0-0",
		"1.0.0-0a",
		"1.0.0-a-b.c--d",
		"1.0.0-alpha.1",
		"1.0.0-x.7.z.92",
		"1.0.0+b1",
		"1.0.0+001",
		"1.0.0-rc.1+build.1-x",
	}
	for _, s := range valid {
		if _, ok := ParseSemver(s); !ok {
			t.Errorf("ParseSemver(%q) failed, want ok", s)
		}
	}
	invalid := []string{
		"",
		"v1.2.3",
		"1.2",
		"1.2.3.4",
		"01.2.3",
		"1.02.3",
		"1.2.03",
		"1.2.3\n",
		" 1.2.3",
		"1.2.3-01",
		"1.2.3-",
		"1.2.3-a..b",
		"1.2.3-a.",
		"1.2.3+",
		"1.2.3+a..b",
		"1.2.3+a_b",
		"1.2.3-a_b",
		"1.2.3-é",
		"1.2.٣",
		"1..3",
		"-1.2.3",
		"1.2.3-a+b+c",
	}
	for _, s := range invalid {
		if _, ok := ParseSemver(s); ok {
			t.Errorf("ParseSemver(%q) ok, want failure", s)
		}
	}
}

func TestCompareSemver(t *testing.T) {
	mustParse := func(s string) Semver {
		t.Helper()
		v, ok := ParseSemver(s)
		if !ok {
			t.Fatalf("ParseSemver(%q) failed", s)
		}
		return v
	}
	ascending := []string{
		"0.0.0",
		"0.0.1",
		"0.1.0",
		"0.9.0",
		"0.10.0",
		"1.0.0-0",
		"1.0.0-2",
		"1.0.0-10",
		"1.0.0-0a",
		"1.0.0-alpha",
		"1.0.0-alpha.1",
		"1.0.0-alpha.beta",
		"1.0.0-beta",
		"1.0.0-beta.2",
		"1.0.0-beta.11",
		"1.0.0-rc.1",
		"1.0.0",
		"2.0.0",
		"99999999999999999999.0.0",
		"100000000000000000000.0.0",
	}
	for i := range ascending {
		for j := range ascending {
			a, b := mustParse(ascending[i]), mustParse(ascending[j])
			want := 0
			switch {
			case i < j:
				want = -1
			case i > j:
				want = 1
			}
			if got := CompareSemver(a, b); got != want {
				t.Errorf("CompareSemver(%q, %q) = %d, want %d", ascending[i], ascending[j], got, want)
			}
		}
	}
	equal := [][2]string{
		{"1.0.0+b1", "1.0.0+b2"},
		{"1.0.0+b1", "1.0.0"},
		{"1.0.0-rc.1+x", "1.0.0-rc.1+y"},
	}
	for _, p := range equal {
		if got := CompareSemver(mustParse(p[0]), mustParse(p[1])); got != 0 {
			t.Errorf("CompareSemver(%q, %q) = %d, want 0", p[0], p[1], got)
		}
	}
}
