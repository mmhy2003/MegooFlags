package sync

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	gosync "sync"
	"testing"

	"github.com/mmhy2003/MegooFlags/cli/client"
)

// store is a stateful in-memory API for the endpoints Snapshot and Apply
// use. Configs in an environment that requires approvals answer 409
// approval_required, and a PUT with an old version 409 version_conflict.
// It checks the references a config makes, so a config that runs before
// the flags it needs fails as it would on the server.
type store struct {
	t  *testing.T
	mu gosync.Mutex

	key      string // the project the store answers for
	project  *client.Project
	segments map[string]*client.Segment
	flags    map[string]*storedFlag

	// requests is every request as "METHOD /path"; writes counts the
	// requests that are not GETs.
	requests []string
	writes   int
	// conflicts is how many times a PUT of config "flag/env" meets a
	// version that someone else has just moved on.
	conflicts map[string]int
	// failCreate lists flag keys whose create fails with 422.
	failCreate map[string]bool
	// changeRequests are the change requests opened, in order.
	changeRequests []client.ChangeRequestCreate
}

type storedFlag struct {
	flag    client.Flag
	configs map[string]*client.FlagConfig
}

// newStore serves project key, which does not exist yet.
func newStore(t *testing.T, key string) (*store, *client.Client) {
	t.Helper()
	s := &store{
		t: t, key: key,
		segments:   map[string]*client.Segment{},
		flags:      map[string]*storedFlag{},
		conflicts:  map[string]int{},
		failCreate: map[string]bool{},
	}
	srv := httptest.NewServer(http.HandlerFunc(s.serve))
	t.Cleanup(srv.Close)
	return s, client.New(srv.URL, "test-token", client.WithMaxRetries(0))
}

// writesTo lists the write requests, in order.
func (s *store) writesTo() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []string
	for _, r := range s.requests {
		if !strings.HasPrefix(r, "GET ") {
			out = append(out, r)
		}
	}
	return out
}

type apiError struct {
	status        int
	code, message string
}

func notFound() *apiError { return &apiError{404, "not_found", "Not found"} }

func invalid(format string, args ...any) *apiError {
	return &apiError{422, "validation_error", fmt.Sprintf(format, args...)}
}

func (s *store) serve(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, r.Method+" "+r.URL.Path)
	if r.Method != http.MethodGet {
		s.writes++
	}
	out, apiErr := s.route(r)
	w.Header().Set("Content-Type", "application/json")
	if apiErr != nil {
		w.WriteHeader(apiErr.status)
		_ = json.NewEncoder(w).Encode(map[string]string{"code": apiErr.code, "message": apiErr.message})
		return
	}
	if out == nil {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method == http.MethodPost {
		w.WriteHeader(http.StatusCreated)
	}
	_ = json.NewEncoder(w).Encode(out)
}

func decode[T any](r *http.Request) (T, *apiError) {
	var v T
	if err := json.NewDecoder(r.Body).Decode(&v); err != nil {
		return v, invalid("body: %v", err)
	}
	return v, nil
}

func page[T any](items []T) any {
	if items == nil {
		items = []T{}
	}
	return map[string]any{"items": items, "next_cursor": nil}
}

func (s *store) route(r *http.Request) (any, *apiError) {
	m := r.Method
	if r.URL.Path == "/api/projects" && m == http.MethodPost {
		return s.createProject(r)
	}
	rest, ok := strings.CutPrefix(r.URL.Path, "/api/projects/"+s.key)
	if !ok || s.project == nil {
		return nil, notFound()
	}
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if parts[0] == "" {
		parts = nil
	}
	switch {
	case len(parts) == 0 && m == http.MethodGet:
		return s.project, nil
	case len(parts) == 0 && m == http.MethodPatch:
		in, err := decode[client.ProjectUpdate](r)
		if err != nil {
			return nil, err
		}
		if in.Name != nil {
			s.project.Name = *in.Name
		}
		if in.Description != nil {
			s.project.Description = *in.Description
		}
		return s.project, nil
	case parts[0] == "environments":
		return s.environment(r, parts[1:])
	case parts[0] == "segments":
		return s.segment(r, parts[1:])
	case parts[0] == "flags":
		return s.flag(r, parts[1:])
	case len(parts) == 1 && parts[0] == "change-requests" && m == http.MethodPost:
		in, err := decode[client.ChangeRequestCreate](r)
		if err != nil {
			return nil, err
		}
		s.changeRequests = append(s.changeRequests, in)
		return client.ChangeRequest{ID: fmt.Sprintf("cr-%d", len(s.changeRequests)), Title: in.Title, Kind: in.Kind}, nil
	}
	return nil, notFound()
}

func (s *store) createProject(r *http.Request) (any, *apiError) {
	in, err := decode[client.ProjectCreate](r)
	if err != nil {
		return nil, err
	}
	if in.Key != s.key || s.project != nil {
		return nil, &apiError{409, "conflict", "project exists"}
	}
	s.project = &client.Project{Key: in.Key, Name: in.Name, Description: in.Description, Environments: []client.Environment{}}
	envs := in.Environments
	if len(envs) == 0 {
		envs = []client.EnvironmentCreate{{Key: "development", Name: "Development"}, {Key: "production", Name: "Production"}}
	}
	for _, e := range envs {
		s.addEnvironment(e)
	}
	return s.project, nil
}

func (s *store) addEnvironment(e client.EnvironmentCreate) client.Environment {
	env := client.Environment{Key: e.Key, Name: e.Name, Description: e.Description, SortOrder: len(s.project.Environments)}
	s.project.Environments = append(s.project.Environments, env)
	for _, f := range s.flags {
		f.configs[e.Key] = defaultConfig(e.Key, f.flag.OffVariation)
	}
	return env
}

func (s *store) envIndex(key string) int {
	return slices.IndexFunc(s.project.Environments, func(e client.Environment) bool { return e.Key == key })
}

func (s *store) environment(r *http.Request, parts []string) (any, *apiError) {
	switch {
	case len(parts) == 0 && r.Method == http.MethodPost:
		in, err := decode[client.EnvironmentCreate](r)
		if err != nil {
			return nil, err
		}
		if s.envIndex(in.Key) >= 0 {
			return nil, &apiError{409, "conflict", "environment exists"}
		}
		return s.addEnvironment(in), nil
	case len(parts) == 1 && r.Method == http.MethodPatch:
		i := s.envIndex(parts[0])
		if i < 0 {
			return nil, notFound()
		}
		in, err := decode[client.EnvironmentUpdate](r)
		if err != nil {
			return nil, err
		}
		e := &s.project.Environments[i]
		if in.Name != nil {
			e.Name = *in.Name
		}
		if in.Description != nil {
			e.Description = *in.Description
		}
		if in.ApprovalsRequired != nil {
			e.ApprovalsRequired = *in.ApprovalsRequired
		}
		return e, nil
	}
	return nil, notFound()
}

func (s *store) segment(r *http.Request, parts []string) (any, *apiError) {
	m := r.Method
	if len(parts) == 0 {
		switch m {
		case http.MethodGet:
			var items []client.Segment
			for _, key := range sortedKeys(s.segments) {
				seg := *s.segments[key]
				seg.Included, seg.Excluded, seg.Rules = nil, nil, nil
				items = append(items, seg)
			}
			return page(items), nil
		case http.MethodPost:
			in, err := decode[client.SegmentCreate](r)
			if err != nil {
				return nil, err
			}
			if _, ok := s.segments[in.Key]; ok {
				return nil, &apiError{409, "conflict", "segment exists"}
			}
			s.segments[in.Key] = &client.Segment{Key: in.Key, Name: in.Name, Description: in.Description}
			return s.segments[in.Key], nil
		}
		return nil, notFound()
	}
	seg, ok := s.segments[parts[0]]
	if !ok || len(parts) > 1 {
		return nil, notFound()
	}
	switch m {
	case http.MethodGet:
		return seg, nil
	case http.MethodPatch:
		in, err := decode[client.SegmentUpdate](r)
		if err != nil {
			return nil, err
		}
		if in.Name != nil {
			seg.Name = *in.Name
		}
		if in.Description != nil {
			seg.Description = *in.Description
		}
		return seg, nil
	case http.MethodPut:
		in, err := decode[client.SegmentConfig](r)
		if err != nil {
			return nil, err
		}
		seg.Included, seg.Excluded, seg.Rules = in.Included, in.Excluded, in.Rules
		return seg, nil
	case http.MethodDelete:
		delete(s.segments, parts[0])
		return nil, nil
	}
	return nil, notFound()
}

func defaultConfig(env, off string) *client.FlagConfig {
	return &client.FlagConfig{Environment: env, Version: 1, Fallthrough: client.Serve{Variation: &off}}
}

func (s *store) flag(r *http.Request, parts []string) (any, *apiError) {
	m := r.Method
	if len(parts) == 0 {
		switch m {
		case http.MethodGet:
			archived := r.URL.Query().Get("archived") == "true"
			var items []client.FlagSummary
			for _, key := range sortedKeys(s.flags) {
				if f := s.flags[key].flag; f.Archived == archived {
					items = append(items, f.FlagSummary)
				}
			}
			return page(items), nil
		case http.MethodPost:
			return s.createFlag(r)
		}
		return nil, notFound()
	}
	sf, ok := s.flags[parts[0]]
	if !ok {
		return nil, notFound()
	}
	f := &sf.flag
	sub := ""
	if len(parts) > 1 {
		sub = parts[1]
	}
	switch {
	case len(parts) == 1 && m == http.MethodGet:
		return f, nil
	case len(parts) == 1 && m == http.MethodPatch:
		var in map[string]any
		if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
			return nil, invalid("body: %v", err)
		}
		for k, v := range in {
			switch k {
			case "name":
				f.Name = v.(string)
			case "description":
				f.Description = v.(string)
			case "tags":
				f.Tags = []string{}
				for _, t := range v.([]any) {
					f.Tags = append(f.Tags, t.(string))
				}
			case "owner":
				if v == nil {
					f.Owner = nil
				} else {
					o := v.(string)
					f.Owner = &o
				}
			case "off_variation":
				if !hasVariation(f, v.(string)) {
					return nil, invalid("off_variation: unknown variation %s", v)
				}
				f.OffVariation = v.(string)
			}
		}
		return f, nil
	case len(parts) == 2 && sub == "lifecycle" && m == http.MethodPut:
		in, err := decode[struct{ Permanent bool }](r)
		if err != nil {
			return nil, err
		}
		f.Lifecycle = "active"
		if in.Permanent {
			f.Lifecycle = "permanent"
		}
		return f, nil
	case len(parts) == 2 && (sub == "archive" || sub == "restore") && m == http.MethodPost:
		f.Archived = sub == "archive"
		return f, nil
	case sub == "variations":
		return s.variation(r, f, parts[2:])
	case len(parts) == 3 && sub == "environments":
		return s.config(r, parts[0], parts[2])
	}
	return nil, notFound()
}

func (s *store) createFlag(r *http.Request) (any, *apiError) {
	in, err := decode[client.FlagCreate](r)
	if err != nil {
		return nil, err
	}
	if s.failCreate[in.Key] {
		return nil, invalid("flag %s cannot be created", in.Key)
	}
	if _, ok := s.flags[in.Key]; ok {
		return nil, &apiError{409, "conflict", "flag exists"}
	}
	f := client.Flag{FlagSummary: client.FlagSummary{
		Key: in.Key, Name: in.Name, Description: in.Description, Type: in.Type,
		Tags: in.Tags, Owner: in.Owner, Lifecycle: "active",
	}}
	for _, v := range in.Variations {
		f.Variations = append(f.Variations, client.Variation{Key: v.Key, Name: v.Name, Description: v.Description, Value: v.Value})
	}
	if in.OffVariation == nil || !hasVariation(&f, *in.OffVariation) {
		return nil, invalid("off_variation: unknown variation")
	}
	f.OffVariation = *in.OffVariation
	sf := &storedFlag{flag: f, configs: map[string]*client.FlagConfig{}}
	for _, e := range s.project.Environments {
		sf.configs[e.Key] = defaultConfig(e.Key, f.OffVariation)
	}
	s.flags[in.Key] = sf
	return &sf.flag, nil
}

func hasVariation(f *client.Flag, key string) bool {
	return slices.ContainsFunc(f.Variations, func(v client.Variation) bool { return v.Key == key })
}

func (s *store) variation(r *http.Request, f *client.Flag, parts []string) (any, *apiError) {
	if len(parts) == 0 && r.Method == http.MethodPost {
		in, err := decode[client.VariationIn](r)
		if err != nil {
			return nil, err
		}
		v := client.Variation{Key: in.Key, Name: in.Name, Description: in.Description, Value: in.Value}
		f.Variations = append(f.Variations, v)
		return v, nil
	}
	if len(parts) != 1 {
		return nil, notFound()
	}
	i := slices.IndexFunc(f.Variations, func(v client.Variation) bool { return v.Key == parts[0] })
	if i < 0 {
		return nil, notFound()
	}
	switch r.Method {
	case http.MethodPatch:
		in, err := decode[client.VariationUpdate](r)
		if err != nil {
			return nil, err
		}
		v := &f.Variations[i]
		if in.Name != nil {
			v.Name = *in.Name
		}
		if in.Description != nil {
			v.Description = *in.Description
		}
		if in.Value != nil {
			v.Value = *in.Value
		}
		return v, nil
	case http.MethodDelete:
		f.Variations = slices.Delete(f.Variations, i, i+1)
		return nil, nil
	}
	return nil, notFound()
}

func (s *store) config(r *http.Request, flag, env string) (any, *apiError) {
	sf := s.flags[flag]
	cfg, ok := sf.configs[env]
	if !ok {
		return nil, notFound()
	}
	switch r.Method {
	case http.MethodGet:
		return cfg, nil
	case http.MethodPut:
		in, err := decode[client.FlagConfigIn](r)
		if err != nil {
			return nil, err
		}
		if s.project.Environments[s.envIndex(env)].ApprovalsRequired > 0 {
			return nil, &apiError{409, "approval_required", "Changes in " + env + " need an approved change request"}
		}
		if key := flag + "/" + env; s.conflicts[key] > 0 {
			s.conflicts[key]--
			cfg.Version++ // someone else changed it after our GET
		}
		if in.Version != cfg.Version {
			return nil, &apiError{409, "version_conflict", "The config has changed"}
		}
		for i, p := range in.Prerequisites {
			pf, ok := s.flags[p.Flag]
			if !ok {
				return nil, invalid("prerequisites[%d].flag: unknown flag %s", i, p.Flag)
			}
			if !hasVariation(&pf.flag, p.Variation) {
				return nil, invalid("prerequisites[%d].variation: unknown variation %s", i, p.Variation)
			}
		}
		if v := in.Fallthrough.Variation; v != nil && !hasVariation(&sf.flag, *v) {
			return nil, invalid("fallthrough.variation: unknown variation %s", *v)
		}
		*cfg = client.FlagConfig{
			Environment: env, Enabled: in.Enabled, Version: cfg.Version + 1,
			Targets: in.Targets, Rules: in.Rules, Fallthrough: in.Fallthrough, Prerequisites: in.Prerequisites,
		}
		return cfg, nil
	}
	return nil, notFound()
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}
