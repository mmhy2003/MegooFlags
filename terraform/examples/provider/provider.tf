terraform {
  required_providers {
    megooflags = {
      source = "registry.terraform.io/mmhy2003/megooflags"
    }
  }
}

# url and token fall back to MEGOOFLAGS_URL and MEGOOFLAGS_API_TOKEN.
provider "megooflags" {
  url = "https://flags.example.com"
}
