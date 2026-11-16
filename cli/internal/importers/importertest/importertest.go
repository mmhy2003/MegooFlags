// Package importertest holds what the importers' tests share.
package importertest

import (
	"bytes"
	"io/fs"
	"os"
	"path/filepath"
	"testing"
)

// CompareDirs fails t unless the regular files under got and want have the
// same names and bytes.
func CompareDirs(t testing.TB, got, want string) {
	t.Helper()
	g, w := readTree(t, got), readTree(t, want)
	for name, wb := range w {
		gb, ok := g[name]
		if !ok {
			t.Errorf("%s is missing", name)
		} else if !bytes.Equal(gb, wb) {
			t.Errorf("%s =\n%s\nwant\n%s", name, gb, wb)
		}
	}
	for name := range g {
		if _, ok := w[name]; !ok {
			t.Errorf("%s is not expected", name)
		}
	}
}

func readTree(t testing.TB, root string) map[string][]byte {
	t.Helper()
	files := map[string][]byte{}
	err := filepath.WalkDir(root, func(path string, e fs.DirEntry, err error) error {
		if err != nil || e.IsDir() {
			return err
		}
		b, err := os.ReadFile(path)
		rel, _ := filepath.Rel(root, path)
		files[filepath.ToSlash(rel)] = b
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	return files
}
