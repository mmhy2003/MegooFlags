package megooflags

import (
	"strings"
	"testing"
	"time"
)

func TestConfigDefaultsAndClamps(t *testing.T) {
	c := Config{BaseURL: "https://flags.example.com/"}.withDefaults()
	if c.InitTimeout != 5*time.Second {
		t.Errorf("InitTimeout = %v, want 5s", c.InitTimeout)
	}
	if c.PollInterval != 30*time.Second {
		t.Errorf("PollInterval = %v, want 30s", c.PollInterval)
	}
	if c.FlushInterval != 30*time.Second {
		t.Errorf("FlushInterval = %v, want 30s", c.FlushInterval)
	}
	if c.HTTPClient == nil || c.HTTPClient.Timeout != 0 {
		t.Errorf("HTTPClient = %+v, want a client without a global timeout", c.HTTPClient)
	}
	if c.Logger == nil {
		t.Error("Logger is nil")
	}
	if c.BaseURL != "https://flags.example.com" {
		t.Errorf("BaseURL = %q, want the trailing slash trimmed", c.BaseURL)
	}

	cases := []struct {
		name                string
		poll, flush         time.Duration
		wantPoll, wantFlush time.Duration
	}{
		{"low", time.Second, 5 * time.Second, 15 * time.Second, 10 * time.Second},
		{"high", time.Hour, 2 * time.Minute, time.Hour, 60 * time.Second},
		{"in range", 20 * time.Second, 45 * time.Second, 20 * time.Second, 45 * time.Second},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := Config{PollInterval: tc.poll, FlushInterval: tc.flush}.withDefaults()
			if c.PollInterval != tc.wantPoll {
				t.Errorf("PollInterval = %v, want %v", c.PollInterval, tc.wantPoll)
			}
			if c.FlushInterval != tc.wantFlush {
				t.Errorf("FlushInterval = %v, want %v", c.FlushInterval, tc.wantFlush)
			}
		})
	}
}

func TestValidateRefusesClientKey(t *testing.T) {
	key := "mf_client_secret123"
	err := Config{SDKKey: key, BaseURL: "https://flags.example.com"}.withDefaults().validate()
	if err == nil {
		t.Fatal("validate() = nil, want an error for a client key")
	}
	want := "megooflags: a client SDK key (mf_client_…) cannot be used by a server SDK; use a server key (mf_server_…)"
	if err.Error() != want {
		t.Errorf("error = %q, want %q", err, want)
	}

	for _, key := range []string{"", "secret123", "mf_admin_secret123"} {
		err := Config{SDKKey: key, BaseURL: "https://flags.example.com"}.withDefaults().validate()
		if err == nil {
			t.Errorf("validate(%q) = nil, want an invalid key error", key)
			continue
		}
		if key != "" && strings.Contains(err.Error(), key) {
			t.Errorf("error %q contains the key", err)
		}
	}

	if err := (Config{SDKKey: "mf_server_secret123", BaseURL: "https://flags.example.com"}).withDefaults().validate(); err != nil {
		t.Errorf("validate(server key) = %v, want nil", err)
	}
}

func TestValidateBaseURL(t *testing.T) {
	for _, u := range []string{"", "flags.example.com", "ftp://flags.example.com", "://bad"} {
		err := Config{SDKKey: "mf_server_secret123", BaseURL: u}.withDefaults().validate()
		if err == nil {
			t.Errorf("validate(BaseURL %q) = nil, want an error", u)
		}
	}
	for _, u := range []string{"http://localhost:8080", "https://flags.example.com/"} {
		if err := (Config{SDKKey: "mf_server_secret123", BaseURL: u}).withDefaults().validate(); err != nil {
			t.Errorf("validate(BaseURL %q) = %v, want nil", u, err)
		}
	}
}

func TestValidateOffline(t *testing.T) {
	err := Config{SDKKey: "mf_server_secret123", Offline: true}.withDefaults().validate()
	if err == nil {
		t.Fatal("validate(Offline without BootstrapFile or CacheDir) = nil, want an error")
	}
	for _, c := range []Config{
		{SDKKey: "mf_server_secret123", Offline: true, BootstrapFile: "flags.json"},
		{SDKKey: "mf_server_secret123", Offline: true, CacheDir: t.TempDir()},
	} {
		if err := c.withDefaults().validate(); err != nil {
			t.Errorf("validate(%+v) = %v, want nil (no BaseURL is needed offline)", c, err)
		}
	}
}
