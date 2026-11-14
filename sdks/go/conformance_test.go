package megooflags

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/engine"
)

// conformanceDir holds the language-neutral suite every SDK must pass
// (spec/conformance/README.md).
const conformanceDir = "../../spec/conformance"

type caseFile struct {
	SchemaVersion json.Number    `json:"schema_version"`
	Ruleset       any            `json:"ruleset"`
	Tests         []conformCase  `json:"tests"`
	Vectors       []bucketVector `json:"vectors"`
}

type conformCase struct {
	Name         string         `json:"name"`
	Flag         string         `json:"flag"`
	Context      any            `json:"context"`
	Default      any            `json:"default"`
	ExpectedType string         `json:"expected_type"`
	Expected     expectedResult `json:"expected"`
}

type expectedResult struct {
	Value     any            `json:"value"`
	Variant   *string        `json:"variant"`
	Reason    string         `json:"reason"`
	ErrorCode *string        `json:"error_code"`
	Metadata  map[string]any `json:"metadata"`
}

type bucketVector struct {
	Input  string `json:"input"`
	Bucket int    `json:"bucket"`
}

// loadCaseFile decodes a case file with UseNumber and refuses any
// schema_version other than the integer 1.
func loadCaseFile(path string) (*caseFile, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var f caseFile
	if err := dec.Decode(&f); err != nil {
		return nil, fmt.Errorf("%s: %w", filepath.Base(path), err)
	}
	if f.SchemaVersion.String() != "1" {
		return nil, fmt.Errorf("%s: unsupported schema_version %q", filepath.Base(path), f.SchemaVersion)
	}
	return &f, nil
}

func caseFiles(t *testing.T) []string {
	t.Helper()
	files, err := filepath.Glob(conformanceDir + "/cases/*.json")
	if err != nil {
		t.Fatal(err)
	}
	if len(files) == 0 {
		t.Fatal("no conformance case files in " + conformanceDir + "/cases")
	}
	slices.Sort(files)
	return files
}

func TestConformance(t *testing.T) {
	for _, path := range caseFiles(t) {
		stem := strings.TrimSuffix(filepath.Base(path), ".json")
		t.Run(stem, func(t *testing.T) {
			f, err := loadCaseFile(path)
			if err != nil {
				t.Fatal(err)
			}
			raw, err := json.Marshal(f.Ruleset)
			if err != nil {
				t.Fatal(err)
			}
			rs, err := engine.Parse(raw)
			for _, c := range f.Tests {
				t.Run(stem+"::"+c.Name, func(t *testing.T) {
					if err != nil {
						t.Fatalf("ruleset does not parse: %v", err)
					}
					checkCase(t, rs, c)
				})
			}
		})
	}
}

func checkCase(t *testing.T, rs *engine.Ruleset, c conformCase) {
	t.Helper()
	res := engine.Evaluate(rs, c.Flag, c.Context, c.Default, c.ExpectedType)
	want := c.Expected
	if !sameJSON(res.Value, want.Value) {
		t.Errorf("%s: value: got %#v, want %#v", c.Name, res.Value, want.Value)
	}
	if want.Variant == nil {
		if res.Served {
			t.Errorf("%s: variant: got %q, want null", c.Name, res.Variant)
		}
	} else if !res.Served || res.Variant != *want.Variant {
		t.Errorf("%s: variant: got %q (served %v), want %q", c.Name, res.Variant, res.Served, *want.Variant)
	}
	if string(res.Reason) != want.Reason {
		t.Errorf("%s: reason: got %q, want %q", c.Name, res.Reason, want.Reason)
	}
	if want.ErrorCode == nil {
		if res.ErrorCode != engine.ErrNone {
			t.Errorf("%s: error_code: got %q, want null", c.Name, res.ErrorCode)
		}
	} else if string(res.ErrorCode) != *want.ErrorCode {
		t.Errorf("%s: error_code: got %q, want %q", c.Name, res.ErrorCode, *want.ErrorCode)
	}
	meta := res.Metadata()
	for k, v := range want.Metadata {
		got, ok := meta[k]
		if !ok {
			t.Errorf("%s: metadata: %q missing from %v", c.Name, k, meta)
		} else if !sameJSON(got, v) {
			t.Errorf("%s: metadata %q: got %#v, want %#v", c.Name, k, got, v)
		}
	}
}

func TestConformanceBucketing(t *testing.T) {
	f, err := loadCaseFile(conformanceDir + "/bucketing.json")
	if err != nil {
		t.Fatal(err)
	}
	if len(f.Vectors) == 0 {
		t.Fatal("bucketing.json has no vectors")
	}
	for _, v := range f.Vectors {
		t.Run("bucketing::"+v.Input, func(t *testing.T) {
			flag, text, _ := strings.Cut(v.Input, ".")
			if got := engine.Bucket(flag, text); got != v.Bucket {
				t.Errorf("bucketing::%s: got %d, want %d", v.Input, got, v.Bucket)
			}
		})
	}
}

func TestConformanceRefusesUnknownSchemaVersion(t *testing.T) {
	for _, version := range []string{"2", "1.0"} {
		path := filepath.Join(t.TempDir(), "case.json")
		body := `{"schema_version": ` + version + `, "description": "", "ruleset": {}, "tests": []}`
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		_, err := loadCaseFile(path)
		if err == nil || !strings.Contains(err.Error(), "unsupported schema_version") {
			t.Errorf("schema_version %s: got error %v, want unsupported schema_version", version, err)
		}
	}
}

// sameJSON is JSON equality: 1 == 1.0, but true != 1 and "1" != 1.
func sameJSON(a, b any) bool {
	_, aBool := a.(bool)
	_, bBool := b.(bool)
	if aBool || bBool {
		return aBool && bBool && a == b
	}
	af, aNum := toFloat(a)
	bf, bNum := toFloat(b)
	if aNum || bNum {
		return aNum && bNum && af == bf
	}
	as, aStr := a.(string)
	bs, bStr := b.(string)
	if aStr || bStr {
		return aStr && bStr && as == bs
	}
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	if al, ok := a.([]any); ok {
		bl, ok := b.([]any)
		if !ok || len(al) != len(bl) {
			return false
		}
		for i := range al {
			if !sameJSON(al[i], bl[i]) {
				return false
			}
		}
		return true
	}
	if am, ok := a.(map[string]any); ok {
		bm, ok := b.(map[string]any)
		if !ok || len(am) != len(bm) {
			return false
		}
		for k, av := range am {
			bv, ok := bm[k]
			if !ok || !sameJSON(av, bv) {
				return false
			}
		}
		return true
	}
	return false
}

func toFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case json.Number:
		f, err := x.Float64()
		return f, err == nil
	case float64:
		return x, true
	case float32:
		return float64(x), true
	case int:
		return float64(x), true
	case int64:
		return float64(x), true
	}
	return 0, false
}

func TestSameJSON(t *testing.T) {
	tests := []struct {
		a, b  any
		equal bool
	}{
		{json.Number("1"), 1.0, true},
		{true, json.Number("1"), false},
		{"1", json.Number("1"), false},
		{nil, nil, true},
		{nil, false, false},
		{[]any{json.Number("1"), map[string]any{"a": true}}, []any{1.0, map[string]any{"a": true}}, true},
		{[]any{json.Number("1")}, []any{json.Number("1"), json.Number("2")}, false},
		{map[string]any{"a": json.Number("1")}, map[string]any{"a": json.Number("1"), "b": json.Number("2")}, false},
		{map[string]any{"a": json.Number("0")}, map[string]any{"a": false}, false},
	}
	for _, tt := range tests {
		if got := sameJSON(tt.a, tt.b); got != tt.equal {
			t.Errorf("sameJSON(%#v, %#v) = %v, want %v", tt.a, tt.b, got, tt.equal)
		}
	}
}
