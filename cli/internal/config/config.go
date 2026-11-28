// Package config resolves the CLI's connection settings from flags, the
// environment and the config file, in that order.
package config

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"

	"go.yaml.in/yaml/v3"
)

// Values are the resolved settings the commands use.
type Values struct {
	URL, Token, Project, Output string
}

// File is the on-disk config file.
type File struct {
	URL     string `yaml:"url,omitempty"`
	Token   string `yaml:"token,omitempty"`
	Project string `yaml:"project,omitempty"`
	Output  string `yaml:"output,omitempty"`
}

// DefaultOutput is the output format when none is set.
const DefaultOutput = "table"

// DefaultPath returns $XDG_CONFIG_HOME/megooflags/config.yaml, or
// ~/.config/megooflags/config.yaml when XDG_CONFIG_HOME is unset or relative.
func DefaultPath(getenv func(string) string) (string, error) {
	if xdg := getenv("XDG_CONFIG_HOME"); xdg != "" && filepath.IsAbs(xdg) {
		return filepath.Join(xdg, "megooflags", "config.yaml"), nil
	}
	home := getenv("HOME")
	if home == "" {
		home = getenv("USERPROFILE")
	}
	if home == "" {
		return "", errors.New("cannot find the home directory: set HOME or XDG_CONFIG_HOME, or pass --config")
	}
	return filepath.Join(home, ".config", "megooflags", "config.yaml"), nil
}

// Load reads the config file. A missing file gives a zero File and no error.
func Load(path string) (File, error) {
	var f File
	raw, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return f, nil
	}
	if err != nil {
		return f, err
	}
	if err := yaml.Unmarshal(raw, &f); err != nil {
		return File{}, fmt.Errorf("read %s: %w", path, err)
	}
	return f, nil
}

// Save writes the config file atomically: the directory is created with mode
// 0700 and the file with mode 0600, since it can hold an API token.
func Save(path string, f File) error {
	raw, err := yaml.Marshal(f)
	if err != nil {
		return err
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".config-*.yaml")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(raw); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

// Resolve picks each setting from the flags, then the MEGOOFLAGS_URL,
// MEGOOFLAGS_API_TOKEN, MEGOOFLAGS_PROJECT and MEGOOFLAGS_OUTPUT environment
// variables, then the file. Output defaults to "table".
func Resolve(flags Values, getenv func(string) string, file File) Values {
	v := Values{
		URL:     first(flags.URL, getenv("MEGOOFLAGS_URL"), file.URL),
		Token:   first(flags.Token, getenv("MEGOOFLAGS_API_TOKEN"), file.Token),
		Project: first(flags.Project, getenv("MEGOOFLAGS_PROJECT"), file.Project),
		Output:  first(flags.Output, getenv("MEGOOFLAGS_OUTPUT"), file.Output),
	}
	if v.Output == "" {
		v.Output = DefaultOutput
	}
	return v
}

func first(values ...string) string {
	for _, s := range values {
		if s != "" {
			return s
		}
	}
	return ""
}
