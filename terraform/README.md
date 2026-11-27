# MegooFlags Terraform provider

Manage [MegooFlags](https://github.com/mmhy2003/MegooFlags) projects, environments, flags, targeting,
segments and SDK keys with Terraform. The provider uses the management REST API and an API token.

Status: 0.1.0, Beta: the features are complete and tested, and resources and attributes may still change before 1.0.0. The provider is versioned apart from the server.

## Install

The provider is not in the Terraform Registry. Each release, tagged `terraform/vX.Y.Z`, is on the
[GitHub releases page](https://github.com/mmhy2003/MegooFlags/releases) as
`terraform-provider-megooflags_X.Y.Z_<os>_<arch>.zip`, with a `SHA256SUMS` file. Install it into a
filesystem mirror:

1. Download the archive for your system and `SHA256SUMS`, and check the archive:

   ```bash
   v=0.1.0
   gh release download "terraform/v$v" --repo mmhy2003/MegooFlags \
     --pattern "terraform-provider-megooflags_${v}_linux_amd64.zip" --pattern SHA256SUMS
   sha256sum --check --ignore-missing SHA256SUMS
   ```

2. Unzip it into `~/.terraform.d/plugins/registry.terraform.io/mmhy2003/megooflags/<v>/<os>_<arch>/`:

   ```bash
   dir=~/.terraform.d/plugins/registry.terraform.io/mmhy2003/megooflags/$v/linux_amd64
   mkdir -p "$dir" && unzip "terraform-provider-megooflags_${v}_linux_amd64.zip" -d "$dir"
   ```

3. Tell Terraform to take this provider from the mirror, in `~/.terraformrc` (`terraform.rc` in
   `%APPDATA%` on Windows). The path must be absolute:

   ```terraform
   provider_installation {
     filesystem_mirror {
       path    = "/home/you/.terraform.d/plugins"
       include = ["registry.terraform.io/mmhy2003/megooflags"]
     }
     direct {
       exclude = ["registry.terraform.io/mmhy2003/megooflags"]
     }
   }
   ```

## Usage

```terraform
terraform {
  required_providers {
    megooflags = {
      source  = "registry.terraform.io/mmhy2003/megooflags"
      version = "0.1.0"
    }
  }
}

# url and token fall back to MEGOOFLAGS_URL and MEGOOFLAGS_API_TOKEN.
provider "megooflags" {
  url = "https://flags.example.com"
}

resource "megooflags_project" "shop" {
  key  = "shop"
  name = "Shop"
  environments = [
    { key = "dev", name = "Development" },
    { key = "prod", name = "Production" },
  ]
}

resource "megooflags_flag" "new_checkout" {
  project = megooflags_project.shop.key
  key     = "new-checkout"
  type    = "boolean"
  name    = "New checkout"
}

resource "megooflags_flag_environment" "new_checkout_dev" {
  project     = megooflags_project.shop.key
  flag        = megooflags_flag.new_checkout.key
  environment = "dev"
  enabled     = true
  fallthrough = { variation = "true" }
}
```

Export the token with `export MEGOOFLAGS_API_TOKEN=…` rather than writing it in a file, then run
`terraform init` and `terraform apply`.

Terraform cannot change flags in an environment whose `approvals_required` is above 0: the API refuses,
and the provider says so. Make those changes through a change request outside Terraform.

## Documentation

- [Provider](docs/index.md)
- Resources: [megooflags_project](docs/resources/project.md), [megooflags_flag](docs/resources/flag.md),
  [megooflags_flag_environment](docs/resources/flag_environment.md),
  [megooflags_segment](docs/resources/segment.md), [megooflags_sdk_key](docs/resources/sdk_key.md)
- Data sources: [megooflags_project](docs/data-sources/project.md), [megooflags_flag](docs/data-sources/flag.md)

The docs are generated from the schema and `examples/` with
`go tool tfplugindocs generate --provider-name megooflags`.

## Development

```bash
gofmt -l . && go vet ./... && go test -race -count=1 ./...
go build -o bin/terraform-provider-megooflags .
TF_ACC=1 MEGOOFLAGS_E2E_URL=… MEGOOFLAGS_API_TOKEN=… MEGOOFLAGS_URL=$MEGOOFLAGS_E2E_URL \
  go test -count=1 -v -run TestAcc ./...      # against a running stack
```

The provider has its own version, released with `terraform/vX.Y.Z` tags. License: [Apache-2.0](LICENSE).
