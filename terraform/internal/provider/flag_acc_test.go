package provider

import (
	"context"
	"fmt"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

// testAccProjectConfig is a megooflags_project.test with dev and prod.
func testAccProjectConfig(key string) string {
	return fmt.Sprintf(`
resource "megooflags_project" "test" {
  key  = %q
  name = "Terraform acceptance"
  environments = [
    { key = "dev", name = "Development" },
    { key = "prod", name = "Production" },
  ]
}
`, key)
}

func testAccCheckFlagGone() func(ctx context.Context, c *client.Client, a map[string]string) error {
	return func(ctx context.Context, c *client.Client, a map[string]string) error {
		_, err := c.GetFlag(ctx, a["project"], a["key"])
		return err
	}
}

func TestAccFlag_boolean(t *testing.T) {
	project := testAccKey(t)
	name := "megooflags_flag.test"
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProtoV6ProviderFactories,
		CheckDestroy:             testAccCheckGone("megooflags_flag", testAccCheckFlagGone()),
		Steps: []resource.TestStep{
			{
				Config: testAccProjectConfig(project) + `
resource "megooflags_flag" "test" {
  project = megooflags_project.test.key
  key     = "new-checkout"
  type    = "boolean"
  name    = "New checkout"
}
`,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "key", "new-checkout"),
					resource.TestCheckResourceAttr(name, "description", ""),
					resource.TestCheckResourceAttr(name, "tags.#", "0"),
					resource.TestCheckNoResourceAttr(name, "owner"),
					resource.TestCheckResourceAttr(name, "permanent", "false"),
					resource.TestCheckResourceAttr(name, "variations.#", "2"),
					resource.TestCheckResourceAttr(name, "variations.0.value_json", "true"),
					resource.TestCheckResourceAttr(name, "variations.1.value_json", "false"),
					resource.TestCheckResourceAttrSet(name, "off_variation"),
				),
			},
			{
				Config: testAccProjectConfig(project) + `
resource "megooflags_flag" "test" {
  project     = megooflags_project.test.key
  key         = "new-checkout"
  type        = "boolean"
  name        = "New checkout flow"
  description = "Replaces the old checkout"
  tags        = ["checkout", "web"]
  owner       = "team-shop"
  permanent   = true
}
`,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "name", "New checkout flow"),
					resource.TestCheckResourceAttr(name, "description", "Replaces the old checkout"),
					resource.TestCheckTypeSetElemAttr(name, "tags.*", "checkout"),
					resource.TestCheckTypeSetElemAttr(name, "tags.*", "web"),
					resource.TestCheckResourceAttr(name, "owner", "team-shop"),
					resource.TestCheckResourceAttr(name, "permanent", "true"),
					resource.TestCheckResourceAttr(name, "variations.#", "2"),
				),
			},
			{
				ResourceName:                         name,
				ImportState:                          true,
				ImportStateIdFunc:                    testAccImportID(name, "project", "key"),
				ImportStateVerify:                    true,
				ImportStateVerifyIdentifierAttribute: "key",
			},
		},
	})
}

func TestAccFlag_variations(t *testing.T) {
	project := testAccKey(t)
	name := "megooflags_flag.test"
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProtoV6ProviderFactories,
		CheckDestroy:             testAccCheckGone("megooflags_flag", testAccCheckFlagGone()),
		Steps: []resource.TestStep{
			{
				Config: testAccProjectConfig(project) + `
resource "megooflags_flag" "test" {
  project = megooflags_project.test.key
  key     = "banner"
  type    = "json"
  name    = "Banner"
  variations = [
    { key = "none", value_json = jsonencode(null) },
    { key = "sale", name = "Sale", value_json = jsonencode({ text = "Sale", percent = 10 }) },
    { key = "old", value_json = jsonencode("old") },
  ]
  off_variation = "old"
}
`,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "variations.#", "3"),
					resource.TestCheckResourceAttr(name, "variations.1.name", "Sale"),
					resource.TestCheckResourceAttr(name, "off_variation", "old"),
				),
			},
			{
				// Rename one, add one, and delete the off variation after
				// moving off_variation away from it. The first variation
				// stays: it is every environment's fallthrough, so in use.
				Config: testAccProjectConfig(project) + `
resource "megooflags_flag" "test" {
  project = megooflags_project.test.key
  key     = "banner"
  type    = "json"
  name    = "Banner"
  variations = [
    { key = "none", value_json = jsonencode(null) },
    { key = "sale", name = "Big sale", value_json = jsonencode({ percent = 20.0, text = "Sale" }) },
    { key = "empty", value_json = jsonencode({}) },
  ]
  off_variation = "empty"
}
`,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "variations.#", "3"),
					resource.TestCheckResourceAttr(name, "variations.1.key", "sale"),
					resource.TestCheckResourceAttr(name, "variations.1.name", "Big sale"),
					resource.TestCheckResourceAttr(name, "variations.2.key", "empty"),
					resource.TestCheckResourceAttr(name, "off_variation", "empty"),
				),
			},
			{
				ResourceName:                         name,
				ImportState:                          true,
				ImportStateIdFunc:                    testAccImportID(name, "project", "key"),
				ImportStateVerify:                    true,
				ImportStateVerifyIdentifierAttribute: "key",
			},
		},
	})
}
