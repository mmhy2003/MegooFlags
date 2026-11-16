resource "megooflags_flag_environment" "new_checkout_staging" {
  project     = megooflags_project.shop.key
  flag        = megooflags_flag.new_checkout.key
  environment = "staging"
  enabled     = true

  targets = [
    { variation = "true", values = ["alice", "bob"] },
  ]

  rules = [
    {
      description = "Beta testers"
      clauses = [
        { operator = "in_segment", values_json = jsonencode([megooflags_segment.beta.key]) },
      ]
      variation = "true"
    },
  ]

  # 25% of the other users get the new checkout.
  fallthrough = {
    rollout = {
      weights = [
        { variation = "true", weight = 25000 },
        { variation = "false", weight = 75000 },
      ]
    }
  }
}
