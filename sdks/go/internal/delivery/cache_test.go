package delivery

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

const testKey = "srv-secret-key-123"

func TestCacheRoundTrip(t *testing.T) {
	c := NewCache(t.TempDir(), testKey)
	raw := []byte(`{"project":"p","environment":"e","flags":{},"segments":{}}`)
	if err := c.Save(raw, `"v1"`); err != nil {
		t.Fatalf("Save: %v", err)
	}
	got, etag, err := c.Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if string(got) != string(raw) {
		t.Errorf("raw = %s, want %s", got, raw)
	}
	if etag != `"v1"` {
		t.Errorf("etag = %q, want %q", etag, `"v1"`)
	}
}

func TestCacheCreatesDir(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "a", "b")
	c := NewCache(dir, testKey)
	if err := c.Save([]byte(`{}`), "x"); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if _, _, err := c.Load(); err != nil {
		t.Fatalf("Load: %v", err)
	}
}

func TestCacheFileNameHidesKey(t *testing.T) {
	dir := t.TempDir()
	c := NewCache(dir, testKey)
	if err := c.Save([]byte(`{}`), "x"); err != nil {
		t.Fatalf("Save: %v", err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 {
		t.Fatalf("dir has %d entries, want 1", len(entries))
	}
	name := entries[0].Name()
	if strings.Contains(name, testKey) {
		t.Errorf("file name %q contains the SDK key", name)
	}
	if !strings.HasPrefix(name, "megooflags-") || !strings.HasSuffix(name, ".json") || len(name) != len("megooflags-")+16+len(".json") {
		t.Errorf("file name %q, want megooflags-<16 hex>.json", name)
	}
	data, err := os.ReadFile(filepath.Join(dir, name))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), testKey) {
		t.Error("cache file contains the SDK key")
	}
	other := NewCache(dir, "another-key")
	if other.path == c.path {
		t.Error("different keys map to the same file")
	}
}

func TestCacheAtomicReplace(t *testing.T) {
	dir := t.TempDir()
	c := NewCache(dir, testKey)
	if err := c.Save([]byte(`{"n":1}`), "1"); err != nil {
		t.Fatal(err)
	}
	if err := c.Save([]byte(`{"n":2}`), "2"); err != nil {
		t.Fatal(err)
	}
	raw, etag, err := c.Load()
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != `{"n":2}` || etag != "2" {
		t.Errorf("Load = %s %q, want {\"n\":2} \"2\"", raw, etag)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".tmp") {
			t.Errorf("leftover temp file %q", e.Name())
		}
	}
	if len(entries) != 1 {
		t.Errorf("dir has %d entries, want 1", len(entries))
	}
}

func TestCacheLoadMissing(t *testing.T) {
	c := NewCache(t.TempDir(), testKey)
	if _, _, err := c.Load(); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("Load err = %v, want os.ErrNotExist", err)
	}
}

func TestCacheLoadCorrupt(t *testing.T) {
	c := NewCache(t.TempDir(), testKey)
	if err := os.WriteFile(c.path, []byte(`{"etag": "x", "ruleset": `), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := c.Load(); err == nil {
		t.Error("Load of corrupt file returned no error")
	} else if errors.Is(err, os.ErrNotExist) {
		t.Errorf("Load of corrupt file = %v, want a non-ErrNotExist error", err)
	}
}

func TestCacheSaveInvalidRaw(t *testing.T) {
	dir := t.TempDir()
	c := NewCache(dir, testKey)
	if err := c.Save([]byte(`{not json`), "x"); err == nil {
		t.Error("Save of invalid JSON returned no error")
	}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		t.Errorf("unexpected file %q after failed Save", e.Name())
	}
}

func TestNilCache(t *testing.T) {
	c := NewCache("", testKey)
	if c != nil {
		t.Fatalf("NewCache(\"\") = %v, want nil", c)
	}
	if _, _, err := c.Load(); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("nil Load err = %v, want os.ErrNotExist", err)
	}
	if err := c.Save([]byte(`{}`), "x"); err != nil {
		t.Errorf("nil Save err = %v, want nil", err)
	}
}

func TestCacheFileMode(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("file modes are not POSIX on Windows")
	}
	c := NewCache(t.TempDir(), testKey)
	if err := c.Save([]byte(`{}`), "x"); err != nil {
		t.Fatal(err)
	}
	fi, err := os.Stat(c.path)
	if err != nil {
		t.Fatal(err)
	}
	if mode := fi.Mode().Perm(); mode != 0o600 {
		t.Errorf("mode = %o, want 600", mode)
	}
}
