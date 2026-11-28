package sync

import (
	"bytes"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
)

// printed is the plan's Print output as lines.
func printed(p Plan) []string {
	var buf bytes.Buffer
	p.Print(&buf)
	s := strings.TrimSuffix(buf.String(), "\n")
	if s == "" {
		return nil
	}
	return strings.Split(s, "\n")
}

func sameLines(t *testing.T, got, want []string) {
	t.Helper()
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("plan =\n%s\nwant\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
}

// editFlag changes the flag key of d with edit.
func editFlag(d *flagfiles.Dir, key string, edit func(f *flagfiles.Flag)) {
	f := d.Flags[key]
	edit(&f)
	d.Flags[key] = f
}

// editEnv changes the config of flag key in env with edit.
func editEnv(d *flagfiles.Dir, key, env string, edit func(e *flagfiles.FlagEnv)) {
	editFlag(d, key, func(f *flagfiles.Flag) {
		envs := map[string]flagfiles.FlagEnv{}
		for k, v := range f.Environments {
			envs[k] = v
		}
		e := envs[env]
		edit(&e)
		envs[env] = e
		f.Environments = envs
	})
}

func TestDiffSameIsEmpty(t *testing.T) {
	if p := Diff(expected(), expected(), Options{}); len(p.Changes) != 0 {
		t.Fatalf("changes = %v", printed(p))
	}
}

// The exported files, read back from YAML, mean the same as the server's
// JSON: tag order, ints and nil lists make no difference.
func TestDiffExportedFilesAreEmpty(t *testing.T) {
	files, err := flagfiles.Load(filepath.Join("testdata", "export-golden"))
	if err != nil {
		t.Fatal(err)
	}
	if p := Diff(files, expected(), Options{}); len(p.Changes) != 0 {
		t.Fatalf("changes = %v", printed(p))
	}
}

func TestDiffChangeKinds(t *testing.T) {
	tests := []struct {
		name  string
		files func(d *flagfiles.Dir)
		opts  Options
		want  []string
	}{
		{
			name:  "project details",
			files: func(d *flagfiles.Dir) { d.Project.Project.Name = "Shop 2"; d.Project.Project.Description = "" },
			want:  []string{"~ project shop: name, description"},
		},
		{
			name: "new environment",
			files: func(d *flagfiles.Dir) {
				d.Project.Environments = append(d.Project.Environments, flagfiles.Environment{Key: "staging", Name: "Staging"})
			},
			want: []string{"+ environment staging"},
		},
		{
			name:  "changed environment",
			files: func(d *flagfiles.Dir) { d.Project.Environments[1].ApprovalsRequired = 2 },
			want:  []string{"~ environment production: approvals_required"},
		},
		{
			name:  "server-only environment is kept",
			files: func(d *flagfiles.Dir) { d.Project.Environments = d.Project.Environments[1:] },
			want:  nil,
		},
		{
			name: "environment order makes no difference",
			files: func(d *flagfiles.Dir) {
				e := d.Project.Environments
				d.Project.Environments = []flagfiles.Environment{e[1], e[0]}
			},
			want: nil,
		},
		{
			name: "new segment with config",
			files: func(d *flagfiles.Dir) {
				d.Segments["vip"] = flagfiles.Segment{Key: "vip", Name: "VIP", Included: []string{"u7"}}
			},
			want: []string{"+ segment vip", "~ segment_config vip: included"},
		},
		{
			name: "new empty segment",
			files: func(d *flagfiles.Dir) {
				d.Segments["vip"] = flagfiles.Segment{Key: "vip", Name: "VIP"}
			},
			want: []string{"+ segment vip"},
		},
		{
			name: "segment details",
			files: func(d *flagfiles.Dir) {
				s := d.Segments["beta"]
				s.Description = "Testers"
				d.Segments["beta"] = s
			},
			want: []string{"~ segment beta: description"},
		},
		{
			name: "segment config",
			files: func(d *flagfiles.Dir) {
				s := d.Segments["beta"]
				s.Excluded = nil
				s.Rules = nil
				d.Segments["beta"] = s
			},
			want: []string{"~ segment_config beta: excluded, rules"},
		},
		{
			name: "segment included order makes no difference",
			files: func(d *flagfiles.Dir) {
				s := d.Segments["beta"]
				s.Included = []string{"u2", "u1"}
				d.Segments["beta"] = s
			},
			want: nil,
		},
		{
			name: "new flag",
			files: func(d *flagfiles.Dir) {
				d.Flags["dark-mode"] = flagfiles.Flag{
					Key: "dark-mode", Name: "Dark mode", Type: "boolean", Variations: boolFlag("", ""), OffVariation: "false",
					Environments: map[string]flagfiles.FlagEnv{"development": offEnv("true")},
				}
			},
			want: []string{"+ flag dark-mode", "~ config dark-mode [development]: fallthrough"},
		},
		{
			name: "new permanent archived flag",
			files: func(d *flagfiles.Dir) {
				d.Flags["dark-mode"] = flagfiles.Flag{
					Key: "dark-mode", Name: "Dark mode", Type: "boolean", Variations: boolFlag("", ""), OffVariation: "false",
					Permanent: true, Archived: true,
				}
			},
			want: []string{"+ flag dark-mode", "~ lifecycle dark-mode: permanent", "- flag dark-mode: archive"},
		},
		{
			name: "flag details",
			files: func(d *flagfiles.Dir) {
				editFlag(d, "new-checkout", func(f *flagfiles.Flag) {
					f.Name = "Checkout"
					f.Tags = []string{"web"}
					f.Owner = nil
					f.OffVariation = "true"
				})
			},
			want: []string{"~ flag new-checkout: name, tags, owner, off_variation"},
		},
		{
			name: "tag order makes no difference",
			files: func(d *flagfiles.Dir) {
				editFlag(d, "new-checkout", func(f *flagfiles.Flag) { f.Tags = []string{"checkout", "web"} })
			},
			want: nil,
		},
		{
			name:  "flag type",
			files: func(d *flagfiles.Dir) { editFlag(d, "banner-text", func(f *flagfiles.Flag) { f.Type = "json" }) },
			want:  []string{"~ flag banner-text: type"},
		},
		{
			name:  "lifecycle",
			files: func(d *flagfiles.Dir) { editFlag(d, "banner-text", func(f *flagfiles.Flag) { f.Permanent = false }) },
			want:  []string{"~ lifecycle banner-text: temporary"},
		},
		{
			name:  "archive",
			files: func(d *flagfiles.Dir) { editFlag(d, "new-checkout", func(f *flagfiles.Flag) { f.Archived = true }) },
			want:  []string{"- flag new-checkout: archive"},
		},
		{
			name:  "restore",
			files: func(d *flagfiles.Dir) { editFlag(d, "old-banner", func(f *flagfiles.Flag) { f.Archived = false }) },
			// The lifecycle of an archived flag is unknown, so a restore sets it.
			want: []string{"~ lifecycle old-banner: temporary", "+ flag old-banner: restore"},
		},
		{
			name: "variations",
			files: func(d *flagfiles.Dir) {
				editFlag(d, "banner-text", func(f *flagfiles.Flag) {
					f.Variations = []client.Variation{
						{Key: "hello", Name: "Hi", Value: "Hi!"},
						{Key: "bye", Name: "Bye", Value: "Bye"},
					}
				})
			},
			want: []string{
				"~ variation banner-text/hello: name, value",
				"+ variation banner-text/bye",
				"- variation banner-text/welcome",
			},
		},
		{
			name: "config parts",
			files: func(d *flagfiles.Dir) {
				editEnv(d, "new-checkout", "production", func(e *flagfiles.FlagEnv) {
					e.Rules = e.Rules[:1]
					e.Fallthrough = client.Serve{Variation: ptr("true")}
				})
			},
			want: []string{"~ config new-checkout [production]: rules, fallthrough"},
		},
		{
			name: "every config part",
			files: func(d *flagfiles.Dir) {
				editEnv(d, "new-checkout", "production", func(e *flagfiles.FlagEnv) {
					*e = flagfiles.FlagEnv{Fallthrough: client.Serve{Variation: ptr("true")}}
				})
			},
			want: []string{"~ config new-checkout [production]: enabled, targets, rules, fallthrough, prerequisites"},
		},
		{
			name: "in_segment value order makes no difference",
			files: func(d *flagfiles.Dir) {
				editEnv(d, "new-checkout", "production", func(e *flagfiles.FlagEnv) {
					e.Rules = []client.Rule{e.Rules[0], e.Rules[1]}
					r := e.Rules[0]
					r.Clauses = []client.Clause{{Attribute: "key", Operator: "in_segment", Values: []any{"staff", "beta"}}}
					e.Rules[0] = r
				})
			},
			want: nil,
		},
		{
			name: "other clause value order matters",
			files: func(d *flagfiles.Dir) {
				editEnv(d, "new-checkout", "production", func(e *flagfiles.FlagEnv) {
					e.Rules = []client.Rule{e.Rules[0], e.Rules[1]}
					r := e.Rules[1]
					r.Clauses = []client.Clause{{Attribute: "country", Operator: "in", Values: []any{"ca", "us"}, Negate: true}}
					e.Rules[1] = r
				})
			},
			want: []string{"~ config new-checkout [production]: rules"},
		},
		{
			name: "environment missing from a flag file is unmanaged",
			files: func(d *flagfiles.Dir) {
				editFlag(d, "new-checkout", func(f *flagfiles.Flag) {
					f.Environments = map[string]flagfiles.FlagEnv{"development": f.Environments["development"]}
				})
			},
			want: nil,
		},
		{
			name: "env filter leaves other environments unmanaged",
			files: func(d *flagfiles.Dir) {
				editEnv(d, "new-checkout", "production", func(e *flagfiles.FlagEnv) { e.Enabled = false })
				editEnv(d, "new-checkout", "development", func(e *flagfiles.FlagEnv) { e.Enabled = false })
			},
			opts: Options{Envs: []string{"development"}},
			want: []string{"~ config new-checkout [development]: enabled"},
		},
		{
			name: "flag missing from the files is kept without prune",
			files: func(d *flagfiles.Dir) {
				delete(d.Flags, "banner-text")
				delete(d.Segments, "staff")
			},
			want: nil,
		},
		{
			name: "prune archives live flags and deletes segments",
			files: func(d *flagfiles.Dir) {
				delete(d.Flags, "banner-text")
				delete(d.Flags, "old-banner")
				delete(d.Segments, "staff")
			},
			opts: Options{Prune: true},
			want: []string{"- flag banner-text: archive", "- segment staff"},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			files := expected()
			tt.files(files)
			sameLines(t, printed(Diff(files, expected(), tt.opts)), tt.want)
		})
	}
}

// One and one point zero are the same number, in values and clauses.
func TestDiffNumbersByMeaning(t *testing.T) {
	number := func(one any) *flagfiles.Dir {
		d := expected()
		d.Flags["limit"] = flagfiles.Flag{
			Key: "limit", Name: "Limit", Type: "number", Tags: []string{},
			Variations:   []client.Variation{{Key: "one", Value: one}, {Key: "two", Value: 2}},
			OffVariation: "one",
			Environments: map[string]flagfiles.FlagEnv{"production": {
				Rules: []client.Rule{{
					Clauses:   []client.Clause{{Attribute: "age", Operator: "gt", Values: []any{one}}},
					Variation: ptr("two"),
				}},
				Fallthrough: client.Serve{Variation: ptr("one")},
			}},
		}
		return d
	}
	if p := Diff(number(1), number(1.0), Options{}); len(p.Changes) != 0 {
		t.Fatalf("changes = %v", printed(p))
	}
	sameLines(t, printed(Diff(number(1), number(1.5), Options{})), []string{
		"~ variation limit/one: value",
		"~ config limit [production]: rules",
	})
}

func TestDiffOrder(t *testing.T) {
	files := expected()
	files.Project.Project.Name = "Shop 2"
	files.Project.Environments = append(files.Project.Environments, flagfiles.Environment{Key: "staging", Name: "Staging"})
	files.Segments["vip"] = flagfiles.Segment{Key: "vip", Name: "VIP", Included: []string{"u7"}}
	s := files.Segments["beta"]
	s.Name, s.Included = "Testers", []string{"u1"}
	files.Segments["beta"] = s
	delete(files.Segments, "staff")
	files.Flags["a-new"] = flagfiles.Flag{
		Key: "a-new", Name: "A", Type: "boolean", Variations: boolFlag("", ""), OffVariation: "false",
		Environments: map[string]flagfiles.FlagEnv{"production": offEnv("true")},
	}
	editFlag(files, "banner-text", func(f *flagfiles.Flag) {
		f.Name = "Banner"
		f.Permanent = false
		f.Variations = []client.Variation{f.Variations[0], {Key: "bye", Value: "Bye"}}
	})
	editEnv(files, "banner-text", "development", func(e *flagfiles.FlagEnv) { e.Enabled = true })
	delete(files.Flags, "new-checkout")
	editFlag(files, "old-banner", func(f *flagfiles.Flag) { f.Archived = false })

	p := Diff(files, expected(), Options{Prune: true})
	sameLines(t, printed(p), []string{
		"~ project shop: name",
		"+ environment staging",
		"~ segment beta: name",
		"+ segment vip",
		"~ segment_config beta: included",
		"~ segment_config vip: included",
		"+ flag a-new",
		"~ flag banner-text: name",
		"~ lifecycle banner-text: temporary",
		"~ lifecycle old-banner: temporary",
		"+ flag old-banner: restore",
		"+ variation banner-text/bye",
		"~ config a-new [production]: fallthrough",
		"~ config banner-text [development]: enabled",
		"- variation banner-text/welcome",
		"- flag new-checkout: archive",
		"- segment staff",
	})

	c := p.Changes
	if c[0].Op != OpUpdate || c[0].Kind != KindProject || c[0].Project == nil || c[0].Project.Name != "Shop 2" {
		t.Errorf("project change = %+v", c[0])
	}
	if c[1].Op != OpCreate || c[1].Kind != KindEnvironment || c[1].Environment == nil || c[1].Environment.Key != "staging" {
		t.Errorf("environment change = %+v", c[1])
	}
	if c[4].Kind != KindSegmentConfig || c[4].Segment == nil || len(c[4].Segment.Included) != 1 {
		t.Errorf("segment config change = %+v", c[4])
	}
	if c[6].Op != OpCreate || c[6].Kind != KindFlag || c[6].Flag == nil || c[6].Flag.Name != "A" {
		t.Errorf("flag change = %+v", c[6])
	}
	if c[11].Kind != KindVariation || c[11].Key != "banner-text" || c[11].Variation != "bye" ||
		c[11].VariationValue == nil || c[11].VariationValue.Value != "Bye" {
		t.Errorf("variation change = %+v", c[11])
	}
	if c[13].Kind != KindConfig || c[13].Env != "development" || c[13].Config == nil || !c[13].Config.Enabled {
		t.Errorf("config change = %+v", c[13])
	}
	if c[15].Op != OpArchive || c[15].Kind != KindFlag || c[15].Key != "new-checkout" {
		t.Errorf("prune change = %+v", c[15])
	}
	if c[16].Op != OpDelete || c[16].Kind != KindSegment || c[16].Key != "staff" {
		t.Errorf("segment delete = %+v", c[16])
	}
}

// Fields names the differing parts, so that Apply need not read Summary.
func TestDiffChangeFields(t *testing.T) {
	files := expected()
	editFlag(files, "banner-text", func(f *flagfiles.Flag) { f.Type, f.OffVariation = "json", "welcome" })
	p := Diff(files, expected(), Options{})
	if len(p.Changes) != 1 || !slices.Equal(p.Changes[0].Fields, []string{"type", "off_variation"}) {
		t.Fatalf("changes = %+v", p.Changes)
	}
}

// The server groups targets by variation and sorts their values, puts
// rollout weights in variation order and fills in bucket_by; none of that
// is a difference.
func TestDiffTargetsAndRolloutsByMeaning(t *testing.T) {
	server, files := expected(), expected()
	editEnv(server, "banner-text", "production", func(e *flagfiles.FlagEnv) {
		e.Targets = []client.Target{{Variation: "hello", Values: []string{"u0", "u1", "u2"}}, {Variation: "welcome", Values: []string{"u3"}}}
		e.Rules = []client.Rule{{Clauses: []client.Clause{}, Rollout: &client.Rollout{BucketBy: "key", Weights: []client.Weight{{Variation: "hello", Weight: 40000}, {Variation: "welcome", Weight: 60000}}}}}
		e.Fallthrough = client.Serve{Rollout: &client.Rollout{BucketBy: "key", Weights: []client.Weight{{Variation: "hello", Weight: 75000}, {Variation: "welcome", Weight: 25000}}}}
	})
	editEnv(files, "banner-text", "production", func(e *flagfiles.FlagEnv) {
		e.Targets = []client.Target{{Variation: "welcome", Values: []string{"u3"}}, {Variation: "hello", Values: []string{"u2", "u1"}}, {Variation: "hello", Values: []string{"u0"}}}
		e.Rules = []client.Rule{{Clauses: []client.Clause{}, Rollout: &client.Rollout{Weights: []client.Weight{{Variation: "welcome", Weight: 60000}, {Variation: "hello", Weight: 40000}}}}}
		e.Fallthrough = client.Serve{Rollout: &client.Rollout{Weights: []client.Weight{{Variation: "welcome", Weight: 25000}, {Variation: "hello", Weight: 75000}}}}
	})
	if p := Diff(files, server, Options{}); len(p.Changes) != 0 {
		t.Fatalf("changes = %v", printed(p))
	}

	editEnv(files, "banner-text", "production", func(e *flagfiles.FlagEnv) {
		e.Targets = []client.Target{{Variation: "welcome", Values: []string{"u3", "u2"}}, {Variation: "hello", Values: []string{"u1", "u0"}}}
		e.Fallthrough.Rollout.BucketBy = "org"
	})
	sameLines(t, printed(Diff(files, server, Options{})), []string{"~ config banner-text [production]: targets, fallthrough"})
}

// The API shows any archived flag's lifecycle as "archived", so whether it
// is permanent cannot be compared until it is restored.
func TestDiffArchivedFlagLifecycle(t *testing.T) {
	server := expected()
	editFlag(server, "banner-text", func(f *flagfiles.Flag) { f.Archived, f.Permanent = true, false })
	files := expected()
	editFlag(files, "banner-text", func(f *flagfiles.Flag) { f.Archived, f.Permanent = true, true })
	if p := Diff(files, server, Options{}); len(p.Changes) != 0 {
		t.Fatalf("changes = %v", printed(p))
	}

	editFlag(files, "banner-text", func(f *flagfiles.Flag) { f.Archived = false })
	sameLines(t, printed(Diff(files, server, Options{})), []string{
		"~ lifecycle banner-text: permanent",
		"+ flag banner-text: restore",
	})
}

// An archive waits for the configs: one of them may drop the prerequisite
// on the flag that the API would otherwise refuse to archive.
func TestDiffArchiveAfterConfigs(t *testing.T) {
	files := expected()
	editFlag(files, "banner-text", func(f *flagfiles.Flag) { f.Archived = true })
	editEnv(files, "new-checkout", "production", func(e *flagfiles.FlagEnv) { e.Enabled = !e.Enabled })
	sameLines(t, printed(Diff(files, expected(), Options{})), []string{
		"~ config new-checkout [production]: enabled",
		"- flag banner-text: archive",
	})
}
