resource "megooflags_project" "shop" {
  key         = "shop"
  name        = "Shop"
  description = "The web shop"

  environments = [
    { key = "dev", name = "Development" },
    { key = "staging", name = "Staging" },
    # Terraform cannot change flags here: changes need a change request.
    { key = "prod", name = "Production", approvals_required = 1 },
  ]
}
