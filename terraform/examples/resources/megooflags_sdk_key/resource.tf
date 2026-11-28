resource "megooflags_sdk_key" "backend" {
  project     = megooflags_project.shop.key
  environment = "staging"
  name        = "backend"
  kind        = "server"
}

output "backend_sdk_key" {
  value     = megooflags_sdk_key.backend.secret
  sensitive = true
}
