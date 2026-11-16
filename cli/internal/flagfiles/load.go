package flagfiles

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"go.yaml.in/yaml/v3"
)

// Load reads the directory strictly: unknown fields are errors, and every
// error starts with the file's path relative to dir and, when known, its
// line ("flags/x.yaml:12: field foo not found in type flagfiles.Flag").
func Load(dir string) (*Dir, error) {
	d := &Dir{Segments: map[string]Segment{}, Flags: map[string]Flag{}}

	if err := decodeFile(dir, ProjectFileName, &d.Project); err != nil {
		return nil, err
	}
	if d.Project.APIVersion != APIVersion {
		return nil, fmt.Errorf("%s: apiVersion must be %s, not %q",
			withLine(dir, ProjectFileName, "apiVersion"), APIVersion, d.Project.APIVersion)
	}

	segments, err := keyedFiles(dir, "segments")
	if err != nil {
		return nil, err
	}
	for key, rel := range segments {
		var s Segment
		if err := decodeFile(dir, rel, &s); err != nil {
			return nil, err
		}
		s.Key = key
		d.Segments[key] = s
	}

	flags, err := keyedFiles(dir, "flags")
	if err != nil {
		return nil, err
	}
	for key, rel := range flags {
		var f Flag
		if err := decodeFile(dir, rel, &f); err != nil {
			return nil, err
		}
		f.Key = key
		d.Flags[key] = f
	}
	return d, nil
}

// keyedFiles maps each key to the relative path of sub/<key>.yaml. A
// missing sub directory has no files.
func keyedFiles(dir, sub string) (map[string]string, error) {
	entries, err := os.ReadDir(filepath.Join(dir, sub))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	files := map[string]string{}
	for _, e := range entries {
		key, ok := strings.CutSuffix(e.Name(), ".yaml")
		if e.IsDir() || !ok {
			continue
		}
		rel := path.Join(sub, e.Name())
		if !keyPattern.MatchString(key) {
			return nil, fmt.Errorf("%s: the file name must be a key matching %s", rel, keyPattern)
		}
		files[key] = rel
	}
	return files, nil
}

var (
	typeErrorLine   = regexp.MustCompile(`^line (\d+): (.*)$`)
	syntaxErrorLine = regexp.MustCompile(`^yaml: line (\d+): (.*)$`)
)

// decodeFile decodes the single YAML document in dir/rel into out.
func decodeFile(dir, rel string, out any) error {
	raw, err := os.ReadFile(filepath.Join(dir, filepath.FromSlash(rel)))
	if err != nil {
		return err
	}
	dec := yaml.NewDecoder(bytes.NewReader(raw))
	dec.KnownFields(true)
	err = dec.Decode(out)
	if errors.Is(err, io.EOF) {
		return fmt.Errorf("%s: the file is empty", rel)
	}
	var typeErr *yaml.TypeError
	if errors.As(err, &typeErr) {
		errs := make([]error, len(typeErr.Errors))
		for i, msg := range typeErr.Errors {
			if m := typeErrorLine.FindStringSubmatch(msg); m != nil {
				errs[i] = fmt.Errorf("%s:%s: %s", rel, m[1], m[2])
			} else {
				errs[i] = fmt.Errorf("%s: %s", rel, msg)
			}
		}
		return errors.Join(errs...)
	}
	if err != nil {
		if m := syntaxErrorLine.FindStringSubmatch(err.Error()); m != nil {
			return fmt.Errorf("%s:%s: %s", rel, m[1], m[2])
		}
		return fmt.Errorf("%s: %w", rel, err)
	}
	if dec.Decode(new(yaml.Node)) != io.EOF {
		return fmt.Errorf("%s: the file must hold a single YAML document", rel)
	}
	return nil
}

// withLine returns "rel:line" for the line of the top-level field, or rel
// when the field cannot be found.
func withLine(dir, rel, field string) string {
	raw, err := os.ReadFile(filepath.Join(dir, filepath.FromSlash(rel)))
	if err != nil {
		return rel
	}
	var doc yaml.Node
	if yaml.Unmarshal(raw, &doc) != nil || len(doc.Content) == 0 {
		return rel
	}
	m := doc.Content[0]
	for i := 0; i+1 < len(m.Content); i += 2 {
		if m.Content[i].Value == field {
			return rel + ":" + strconv.Itoa(m.Content[i].Line)
		}
	}
	return rel
}
