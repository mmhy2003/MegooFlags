resource "megooflags_segment" "beta" {
  project     = megooflags_project.shop.key
  key         = "beta-testers"
  name        = "Beta testers"
  description = "People who try features first"
  included    = ["alice"]
  excluded    = ["mallory"]

  rules = [
    {
      description = "Staff"
      clauses = [
        { attribute = "email", operator = "ends_with", values_json = jsonencode(["@example.com"]) },
      ]
    },
  ]
}
