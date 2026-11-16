# megooflags CLI

`megooflags` manages [MegooFlags](https://github.com/mmhy2003/MegooFlags) from a terminal or CI: projects,
environments, segments and flags, promotion between environments, local development with an SDK
bootstrap file, flags as code (`export`, `diff`, `apply`) and imports from Unleash, Flagsmith and
LaunchDarkly. It uses only the management REST API and an API token.

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

- [docs/cli.md](https://github.com/mmhy2003/MegooFlags/blob/main/docs/cli.md): install, configuration,
  every command, output formats, exit codes and local development;
- [docs/flags-as-code.md](https://github.com/mmhy2003/MegooFlags/blob/main/docs/flags-as-code.md): the
  YAML files and Git sync;
- [docs/importers.md](https://github.com/mmhy2003/MegooFlags/blob/main/docs/importers.md): imports from
  other tools.

The `client` package is a typed Go client for the management API, also used by the Terraform provider.

The CLI has its own version, released with `cli/vX.Y.Z` tags. License: [Apache-2.0](LICENSE).
