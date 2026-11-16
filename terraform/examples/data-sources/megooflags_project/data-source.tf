data "megooflags_project" "shop" {
  key = "shop"
}

output "environments" {
  value = [for e in data.megooflags_project.shop.environments : e.key]
}
