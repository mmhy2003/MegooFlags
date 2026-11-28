package megooflags

import (
	"errors"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Config configures a Provider. Only SDKKey and BaseURL (or, with Offline, a
// BootstrapFile or CacheDir) are required.
type Config struct {
	SDKKey        string        // mf_server_…; a client key is refused at NewProvider
	BaseURL       string        // e.g. https://flags.example.com
	BootstrapFile string        // optional ruleset JSON file
	CacheDir      string        // optional; last-known ruleset is read at start, written on change
	Offline       bool          // no network: bootstrap/cache only
	InitTimeout   time.Duration // default 5s
	DisableStream bool          // poll only
	PollInterval  time.Duration // default 30s, clamped to >= 15s
	FlushInterval time.Duration // default 30s, clamped 10–60s
	SendContext   bool          // include context in exposures
	HTTPClient    *http.Client  // default &http.Client{}; must not set a Timeout, the stream is long-lived
	Logger        *slog.Logger  // default slog.Default()
}

const (
	defaultInitTimeout   = 5 * time.Second
	defaultPollInterval  = 30 * time.Second
	minPollInterval      = 15 * time.Second
	defaultFlushInterval = 30 * time.Second
	minFlushInterval     = 10 * time.Second
	maxFlushInterval     = 60 * time.Second
)

// withDefaults returns c with the defaults filled in and the intervals
// clamped.
func (c Config) withDefaults() Config {
	if c.InitTimeout <= 0 {
		c.InitTimeout = defaultInitTimeout
	}
	if c.PollInterval <= 0 {
		c.PollInterval = defaultPollInterval
	}
	c.PollInterval = max(c.PollInterval, minPollInterval)
	if c.FlushInterval <= 0 {
		c.FlushInterval = defaultFlushInterval
	}
	c.FlushInterval = min(max(c.FlushInterval, minFlushInterval), maxFlushInterval)
	if c.HTTPClient == nil {
		c.HTTPClient = &http.Client{} // no global timeout: each request carries its own
	}
	if c.Logger == nil {
		c.Logger = slog.Default()
	}
	c.BaseURL = strings.TrimRight(c.BaseURL, "/")
	return c
}

// validate reports a config the provider cannot run with. The messages never
// include the key.
func (c Config) validate() error {
	switch {
	case strings.HasPrefix(c.SDKKey, "mf_client_"):
		return errors.New("megooflags: a client SDK key (mf_client_…) cannot be used by a server SDK; use a server key (mf_server_…)")
	case !strings.HasPrefix(c.SDKKey, "mf_server_"):
		return errors.New("megooflags: invalid SDK key; use a server key (mf_server_…)")
	}
	if c.Offline {
		if c.BootstrapFile == "" && c.CacheDir == "" {
			return errors.New("megooflags: Offline needs a BootstrapFile or a CacheDir")
		}
		return nil
	}
	u, err := url.Parse(c.BaseURL)
	if c.BaseURL == "" || err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return errors.New("megooflags: BaseURL must be an http or https URL")
	}
	return nil
}
