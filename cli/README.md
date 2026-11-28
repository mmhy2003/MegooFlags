# megooflags CLI

`megooflags` manages [MegooFlags](https://github.com/mmhy2003/MegooFlags) from a terminal or CI: projects,
environments, segments and flags, promotion between environments, local development with an SDK
bootstrap file, flags as code (`export`, `diff`, `apply`) and imports from Unleash, Flagsmith and
LaunchDarkly. It uses only the management REST API and an API token.

Status: 0.1.0, Beta: the features are complete and tested, and the commands and flags may still change before 1.0.0. The CLI is versioned apart from the server.

```bash
megooflags login --url https://flags.example.com
megooflags flag list -p shop
megooflags flag on new-checkout -p shop --env staging
megooflags promote new-checkout -p shop --from staging --to production
megooflags ruleset pull -p shop --env development -o ruleset.json
megooflags diff --dir flags -p shop
```

Install it from the `cli/vX.Y.Z` release archives (check them against `SHA256SUMS`), or build it with
`go build -o bin/megooflags ./cmd/megooflags`.

Documentation:

- [The megooflags CLI](https://mmhy2003.github.io/MegooFlags/use/cli/): install, configuration,
  every command, output formats, exit codes and local development;
- [Flags as code](https://mmhy2003.github.io/MegooFlags/use/flags-as-code/): the
  YAML files and Git sync;
- [Migrate: overview and importers](https://mmhy2003.github.io/MegooFlags/migrate/overview/): imports from
  other tools.

The `client` package is a typed Go client for the management API, also used by the Terraform provider.

The CLI has its own version, released with `cli/vX.Y.Z` tags. License: [Apache-2.0](LICENSE).
