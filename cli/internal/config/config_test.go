package config

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func envMap(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

func TestResolvePrecedence(t *testing.T) {
	file := File{URL: "https://file", Token: "file-token", Project: "file-proj", Output: "yaml"}
	env := envMap(map[string]string{
		"MEGOOFLAGS_URL":       "https://env",
		"MEGOOFLAGS_API_TOKEN": "env-token",
		"MEGOOFLAGS_PROJECT":   "env-proj",
		"MEGOOFLAGS_OUTPUT":    "json",
	})

	got := Resolve(Values{URL: "https://flag", Project: "flag-proj"}, env, file)
	want := Values{URL: "https://flag", Token: "env-token", Project: "flag-proj", Output: "json"}
	if got != want {
		t.Fatalf("flags then env: got %+v, want %+v", got, want)
	}

	got = Resolve(Values{}, envMap(nil), file)
	want = Values{URL: "https://file", Token: "file-token", Project: "file-proj", Output: "yaml"}
	if got != want {
		t.Fatalf("file fallback: got %+v, want %+v", got, want)
	}
}

func TestResolveOutputDefaultsToTable(t *testing.T) {
	got := Resolve(Values{}, envMap(nil), File{})
	if got != (Values{Output: "table"}) {
		t.Fatalf("got %+v", got)
	}
}

func TestDefaultPathUsesXDG(t *testing.T) {
	got, err := DefaultPath(envMap(map[string]string{"XDG_CONFIG_HOME": "/xdg"}))
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join("/xdg", "megooflags", "config.yaml"); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestDefaultPathFallsBackToHome(t *testing.T) {
	got, err := DefaultPath(envMap(map[string]string{"HOME": "/home/u", "USERPROFILE": "/home/u"}))
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join("/home/u", ".config", "megooflags", "config.yaml"); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestLoadMissingFile(t *testing.T) {
	f, err := Load(filepath.Join(t.TempDir(), "nope", "config.yaml"))
	if err != nil {
		t.Fatalf("err = %v", err)
	}
	if f != (File{}) {
		t.Fatalf("got %+v", f)
	}
}

func TestSaveThenLoad(t *testing.T) {
	path := filepath.Join(t.TempDir(), "megooflags", "config.yaml")
	in := File{URL: "https://x", Token: "t", Project: "p"}
	if err := Save(path, in); err != nil {
		t.Fatal(err)
	}
	out, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if out != in {
		t.Fatalf("got %+v, want %+v", out, in)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if want := "url: https://x\ntoken: t\nproject: p\n"; string(raw) != want {
		t.Fatalf("file = %q, want %q (empty fields omitted)", raw, want)
	}
}

func TestSaveModes(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX modes")
	}
	dir := filepath.Join(t.TempDir(), "megooflags")
	path := filepath.Join(dir, "config.yaml")
	if err := Save(path, File{Token: "secret"}); err != nil {
		t.Fatal(err)
	}
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Fatalf("file mode = %o", fi.Mode().Perm())
	}
	di, err := os.Stat(dir)
	if err != nil {
		t.Fatal(err)
	}
	if di.Mode().Perm() != 0o700 {
		t.Fatalf("dir mode = %o", di.Mode().Perm())
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Fatalf("leftover temp files: %v", entries)
	}
}

func TestLoadInvalidYAML(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte("url: [\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil {
		t.Fatal("want an error")
	}
}
