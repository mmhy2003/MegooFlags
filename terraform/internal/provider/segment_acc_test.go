package provider

import (
	"context"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

func TestAccSegment_basic(t *testing.T) {
	project := testAccKey(t)
	name := "megooflags_segment.test"
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProtoV6ProviderFactories,
		CheckDestroy: testAccCheckGone("megooflags_segment", func(ctx context.Context, c *client.Client, a map[string]string) error {
			_, err := c.GetSegment(ctx, a["project"], a["key"])
			return err
		}),
		Steps: []resource.TestStep{
			{
				Config: testAccProjectConfig(project) + `
resource "megooflags_segment" "test" {
  project = megooflags_project.test.key
  key     = "beta-testers"
  name    = "Beta testers"
}
`,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "name", "Beta testers"),
					resource.TestCheckResourceAttr(name, "description", ""),
					resource.TestCheckResourceAttr(name, "included.#", "0"),
					resource.TestCheckResourceAttr(name, "rules.#", "0"),
				),
			},
			{
				Config: testAccProjectConfig(project) + `
resource "megooflags_segment" "test" {
  project     = megooflags_project.test.key
  key         = "beta-testers"
  name        = "Beta"
  description = "People who try features first"
  included    = ["alice", "bob"]
  excluded    = ["mallory"]
  rules = [
    {
      description = "Staff"
      clauses = [
        { attribute = "email", operator = "ends_with", values_json = jsonencode(["@example.com"]) },
        { attribute = "level", operator = "gt", values_json = jsonencode([3]) },
      ]
    },
  ]
}
`,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "name", "Beta"),
					resource.TestCheckResourceAttr(name, "description", "People who try features first"),
					resource.TestCheckTypeSetElemAttr(name, "included.*", "alice"),
					resource.TestCheckTypeSetElemAttr(name, "included.*", "bob"),
					resource.TestCheckTypeSetElemAttr(name, "excluded.*", "mallory"),
					resource.TestCheckResourceAttr(name, "rules.0.description", "Staff"),
					resource.TestCheckResourceAttr(name, "rules.0.clauses.#", "2"),
					resource.TestCheckResourceAttr(name, "rules.0.clauses.1.operator", "gt"),
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
