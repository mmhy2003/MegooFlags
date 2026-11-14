package megooflags

import (
	"context"
	"log/slog"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/open-feature/go-sdk/openfeature"
)

const testKey = "mf_server_test123"

func discardLogger() *slog.Logger { return slog.New(slog.DiscardHandler) }

// newTestProvider loads testdata/ruleset.json as the bootstrap file, offline.
func newTestProvider(t *testing.T) *Provider {
	t.Helper()
	return NewProvider(Config{
		SDKKey:        testKey,
		BootstrapFile: filepath.Join("testdata", "ruleset.json"),
		Offline:       true,
		Logger:        discardLogger(),
	})
}

func wantResolution(t *testing.T, d openfeature.ProviderResolutionDetail, reason openfeature.Reason, variant string, code openfeature.ErrorCode) {
	t.Helper()
	got := d.ResolutionDetail()
	if got.Reason != reason || got.Variant != variant || got.ErrorCode != code {
		t.Errorf("resolution = {Reason %q, Variant %q, ErrorCode %q, Message %q}, want {%q, %q, %q}",
			got.Reason, got.Variant, got.ErrorCode, got.ErrorMessage, reason, variant, code)
	}
}

func TestMetadataAndHooks(t *testing.T) {
	p := newTestProvider(t)
	if got := p.Metadata(); got.Name != "MegooFlags" {
		t.Errorf("Metadata() = %+v, want Name MegooFlags", got)
	}
	if got := p.Hooks(); got != nil {
		t.Errorf("Hooks() = %v, want nil", got)
	}
}

func TestBooleanEvaluation(t *testing.T) {
	p := newTestProvider(t)
	d := p.BooleanEvaluation(context.Background(), "bool-flag", false, openfeature.FlattenedContext{"plan": "pro"})
	if !d.Value {
		t.Error("Value = false, want true")
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.TargetingMatchReason, "on", "")
	if want := (openfeature.FlagMetadata{"rule_index": 0}); !reflect.DeepEqual(d.FlagMetadata, want) {
		t.Errorf("FlagMetadata = %v, want %v", d.FlagMetadata, want)
	}

	d = p.BooleanEvaluation(context.Background(), "bool-flag", true, openfeature.FlattenedContext{"plan": "free"})
	if d.Value {
		t.Error("Value = true, want the fallthrough false")
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.DefaultReason, "off", "")
	if len(d.FlagMetadata) != 0 {
		t.Errorf("FlagMetadata = %v, want empty", d.FlagMetadata)
	}
}

func TestTargetingKeyMapsToKey(t *testing.T) {
	p := newTestProvider(t)
	flat := openfeature.FlattenedContext{openfeature.TargetingKey: "alice", "key": "bob"}
	d := p.StringEvaluation(context.Background(), "targeted", "default", flat)
	if d.Value != "hello alice" {
		t.Errorf("Value = %q, want %q", d.Value, "hello alice")
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.TargetingMatchReason, "alice", "")
	if flat["key"] != "bob" {
		t.Errorf("the caller's context was modified: key = %v", flat["key"])
	}

	d = p.StringEvaluation(context.Background(), "targeted", "default", openfeature.FlattenedContext{"key": "alice"})
	if d.Value != "hello alice" {
		t.Errorf("without targetingKey, Value = %q, want the key attribute to target", d.Value)
	}
}

func TestStringEvaluation(t *testing.T) {
	p := newTestProvider(t)
	d := p.StringEvaluation(context.Background(), "string-flag", "default", nil)
	if d.Value != "blue" {
		t.Errorf("Value = %q, want blue", d.Value)
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.StaticReason, "blue", "")
}

func TestTypeMismatch(t *testing.T) {
	p := newTestProvider(t)
	d := p.StringEvaluation(context.Background(), "bool-flag", "default", nil)
	if d.Value != "default" {
		t.Errorf("Value = %q, want the default", d.Value)
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.ErrorReason, "", openfeature.TypeMismatchCode)

	b := p.BooleanEvaluation(context.Background(), "string-flag", true, nil)
	if !b.Value {
		t.Error("Value = false, want the default true")
	}
	wantResolution(t, b.ProviderResolutionDetail, openfeature.ErrorReason, "", openfeature.TypeMismatchCode)
}

func TestIntEvaluation(t *testing.T) {
	p := newTestProvider(t)
	d := p.IntEvaluation(context.Background(), "int-flag", 7, nil)
	if d.Value != 3 {
		t.Errorf("Value = %d, want 3", d.Value)
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.StaticReason, "three", "")

	d = p.IntEvaluation(context.Background(), "float-flag", 7, nil)
	if d.Value != 7 {
		t.Errorf("Value = %d, want the default 7", d.Value)
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.ErrorReason, "", openfeature.TypeMismatchCode)
	if msg := d.ResolutionDetail().ErrorMessage; msg != "not an integer" {
		t.Errorf("ErrorMessage = %q, want %q", msg, "not an integer")
	}
}

func TestFloatEvaluation(t *testing.T) {
	p := newTestProvider(t)
	d := p.FloatEvaluation(context.Background(), "float-flag", 1, nil)
	if d.Value != 2.5 {
		t.Errorf("Value = %v, want 2.5", d.Value)
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.StaticReason, "half", "")

	d = p.FloatEvaluation(context.Background(), "int-flag", 1, nil)
	if d.Value != 3 {
		t.Errorf("Value = %v, want 3", d.Value)
	}
}

func TestObjectEvaluation(t *testing.T) {
	p := newTestProvider(t)
	d := p.ObjectEvaluation(context.Background(), "json-flag", nil, nil)
	want := map[string]any{"limit": float64(10), "tags": []any{"a", "b"}}
	if !reflect.DeepEqual(d.Value, want) {
		t.Errorf("Value = %#v, want %#v", d.Value, want)
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.StaticReason, "cfg", "")
}

func TestPrerequisiteFailedReason(t *testing.T) {
	p := newTestProvider(t)
	d := p.BooleanEvaluation(context.Background(), "needs-prereq", true, nil)
	if d.Value {
		t.Error("Value = true, want the off variation false")
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.Reason("PREREQUISITE_FAILED"), "off", "")
	if want := (openfeature.FlagMetadata{"prerequisite": "prereq-off"}); !reflect.DeepEqual(d.FlagMetadata, want) {
		t.Errorf("FlagMetadata = %v, want %v", d.FlagMetadata, want)
	}
}

func TestFlagNotFound(t *testing.T) {
	p := newTestProvider(t)
	d := p.StringEvaluation(context.Background(), "missing", "default", nil)
	if d.Value != "default" {
		t.Errorf("Value = %q, want the default", d.Value)
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.ErrorReason, "", openfeature.FlagNotFoundCode)
}

func TestNoRulesetNotReady(t *testing.T) {
	p := NewProvider(Config{SDKKey: testKey, BaseURL: "http://localhost:1", Logger: discardLogger()})
	d := p.BooleanEvaluation(context.Background(), "bool-flag", true, nil)
	if !d.Value {
		t.Error("Value = false, want the default true")
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.ErrorReason, "", openfeature.ProviderNotReadyCode)
}

func TestInvalidBootstrapNotReady(t *testing.T) {
	path := filepath.Join(t.TempDir(), "bad.json")
	if err := os.WriteFile(path, []byte(`{"flags": 1}`), 0o600); err != nil {
		t.Fatal(err)
	}
	p := NewProvider(Config{SDKKey: testKey, BootstrapFile: path, Offline: true, Logger: discardLogger()})
	d := p.IntEvaluation(context.Background(), "int-flag", 7, nil)
	if d.Value != 7 {
		t.Errorf("Value = %d, want the default 7", d.Value)
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.ErrorReason, "", openfeature.ProviderNotReadyCode)
}

func TestFatalConfigEvaluatesDefault(t *testing.T) {
	p := NewProvider(Config{
		SDKKey:        "mf_client_test123",
		BootstrapFile: filepath.Join("testdata", "ruleset.json"),
		Offline:       true,
		Logger:        discardLogger(),
	})
	d := p.StringEvaluation(context.Background(), "string-flag", "default", nil)
	if d.Value != "default" {
		t.Errorf("Value = %q, want the default", d.Value)
	}
	wantResolution(t, d.ProviderResolutionDetail, openfeature.ErrorReason, "", openfeature.ProviderFatalCode)
}
