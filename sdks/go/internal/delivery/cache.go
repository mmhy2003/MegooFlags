package delivery

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// Cache is the last-known ruleset on disk. The file name is derived from a
// hash of the SDK key, so environments never mix and the key is never
// written to disk.
type Cache struct {
	path string
}

type cacheFile struct {
	ETag    string          `json:"etag"`
	Ruleset json.RawMessage `json:"ruleset"`
}

// NewCache returns a cache in dir for sdkKey, or nil when dir is "".
func NewCache(dir, sdkKey string) *Cache {
	if dir == "" {
		return nil
	}
	sum := sha256.Sum256([]byte(sdkKey))
	name := "megooflags-" + hex.EncodeToString(sum[:])[:16] + ".json"
	return &Cache{path: filepath.Join(dir, name)}
}

// Load reads the cached ruleset and its ETag. A missing file, or a nil
// cache, returns an error wrapping os.ErrNotExist.
func (c *Cache) Load() (raw []byte, etag string, err error) {
	if c == nil {
		return nil, "", os.ErrNotExist
	}
	data, err := os.ReadFile(c.path)
	if err != nil {
		return nil, "", err
	}
	var f cacheFile
	if err := json.Unmarshal(data, &f); err != nil {
		return nil, "", fmt.Errorf("megooflags: corrupt cache file %s: %w", c.path, err)
	}
	return f.Ruleset, f.ETag, nil
}

// Save writes raw and etag to a temp file in the cache directory and renames
// it over the cache file, so a reader never sees a partial write. A nil
// cache saves nothing.
func (c *Cache) Save(raw []byte, etag string) (err error) {
	if c == nil {
		return nil
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(cacheFile{ETag: etag, Ruleset: json.RawMessage(raw)}); err != nil {
		return fmt.Errorf("megooflags: encode cache: %w", err)
	}
	dir := filepath.Dir(c.path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".megooflags-*.tmp")
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = tmp.Close()
			_ = os.Remove(tmp.Name())
		}
	}()
	if _, err = tmp.Write(buf.Bytes()); err != nil {
		return err
	}
	if err = tmp.Sync(); err != nil {
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	if err = os.Chmod(tmp.Name(), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), c.path)
}
