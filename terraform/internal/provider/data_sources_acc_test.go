package provider

import (
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
)

func TestAccDataSources(t *testing.T) {
	project := testAccKey(t)
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProtoV6ProviderFactories,
		Steps: []resource.TestStep{
			{
				Config: testAccProjectConfig(project) + `
resource "megooflags_flag" "test" {
  project     = megooflags_project.test.key
  key         = "theme"
  type        = "string"
  name        = "Theme"
  description = "Colour scheme"
  tags        = ["ui"]
  owner       = "team-web"
  variations = [
    { key = "light", value_json = jsonencode("light") },
    { key = "dark", name = "Dark", value_json = jsonencode("dark") },
  ]
  off_variation = "light"
}

data "megooflags_project" "test" {
  key = megooflags_project.test.key
}

data "megooflags_flag" "test" {
  project = megooflags_flag.test.project
  key     = megooflags_flag.test.key
}
`,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttrPair("data.megooflags_project.test", "name", "megooflags_project.test", "name"),
					resource.TestCheckResourceAttrPair("data.megooflags_project.test", "stale_after_days", "megooflags_project.test", "stale_after_days"),
					resource.TestCheckResourceAttr("data.megooflags_project.test", "environments.#", "2"),
					resource.TestCheckResourceAttr("data.megooflags_project.test", "environments.1.key", "prod"),
					resource.TestCheckResourceAttr("data.megooflags_project.test", "environments.1.approvals_required", "0"),
					resource.TestCheckResourceAttr("data.megooflags_flag.test", "type", "string"),
					resource.TestCheckResourceAttr("data.megooflags_flag.test", "name", "Theme"),
					resource.TestCheckResourceAttr("data.megooflags_flag.test", "description", "Colour scheme"),
					resource.TestCheckResourceAttr("data.megooflags_flag.test", "tags.0", "ui"),
					resource.TestCheckResourceAttr("data.megooflags_flag.test", "owner", "team-web"),
					resource.TestCheckResourceAttr("data.megooflags_flag.test", "variations.#", "2"),
					resource.TestCheckResourceAttr("data.megooflags_flag.test", "variations.1.name", "Dark"),
					resource.TestCheckResourceAttr("data.megooflags_flag.test", "variations.1.value_json", `"dark"`),
					resource.TestCheckResourceAttr("data.megooflags_flag.test", "off_variation", "light"),
					resource.TestCheckResourceAttr("data.megooflags_flag.test", "permanent", "false"),
					resource.TestCheckResourceAttr("data.megooflags_flag.test", "archived", "false"),
				),
			},
		},
	})
}
