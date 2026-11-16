data "megooflags_flag" "theme" {
  project = "shop"
  key     = "theme"
}

output "theme_values" {
  value = [for v in data.megooflags_flag.theme.variations : jsondecode(v.value_json)]
}
