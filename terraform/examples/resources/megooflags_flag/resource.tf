resource "megooflags_flag" "new_checkout" {
  project     = megooflags_project.shop.key
  key         = "new-checkout"
  type        = "boolean"
  name        = "New checkout"
  description = "Replaces the old checkout"
  tags        = ["checkout"]
  owner       = "team-shop"
}

resource "megooflags_flag" "theme" {
  project = megooflags_project.shop.key
  key     = "theme"
  type    = "string"
  name    = "Theme"

  variations = [
    { key = "light", name = "Light", value_json = jsonencode("light") },
    { key = "dark", name = "Dark", value_json = jsonencode("dark") },
  ]
  off_variation = "light"
}
