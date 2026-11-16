package provider

import (
	"context"
	"fmt"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/terraform"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

func TestAccProject_basic(t *testing.T) {
	key := testAccKey(t)
	name := "megooflags_project.test"
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProtoV6ProviderFactories,
		CheckDestroy: testAccCheckGone("megooflags_project", func(ctx context.Context, c *client.Client, a map[string]string) error {
			_, err := c.GetProject(ctx, a["key"])
			return err
		}),
		Steps: []resource.TestStep{
			{
				Config: fmt.Sprintf(`
resource "megooflags_project" "test" {
  key  = %q
  name = "Shop"
  environments = [
    { key = "dev", name = "Development" },
    { key = "prod", name = "Production" },
  ]
}
`, key),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "key", key),
					resource.TestCheckResourceAttr(name, "name", "Shop"),
					resource.TestCheckResourceAttr(name, "description", ""),
					resource.TestCheckResourceAttr(name, "stale_after_days", "30"),
					resource.TestCheckResourceAttr(name, "environments.#", "2"),
					resource.TestCheckResourceAttr(name, "environments.1.key", "prod"),
					resource.TestCheckResourceAttr(name, "environments.1.approvals_required", "0"),
				),
			},
			{
				Config: fmt.Sprintf(`
resource "megooflags_project" "test" {
  key              = %q
  name             = "Shop 2"
  description      = "The web shop"
  stale_after_days = 60
  environments = [
    { key = "dev", name = "Dev", description = "Local and preview" },
    { key = "staging", name = "Staging", approvals_required = 2 },
  ]
}
`, key),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "name", "Shop 2"),
					resource.TestCheckResourceAttr(name, "description", "The web shop"),
					resource.TestCheckResourceAttr(name, "stale_after_days", "60"),
					resource.TestCheckResourceAttr(name, "environments.#", "2"),
					resource.TestCheckResourceAttr(name, "environments.0.name", "Dev"),
					resource.TestCheckResourceAttr(name, "environments.0.description", "Local and preview"),
					resource.TestCheckResourceAttr(name, "environments.1.key", "staging"),
					resource.TestCheckResourceAttr(name, "environments.1.approvals_required", "2"),
					func(_ *terraform.State) error {
						envs, err := testAccClient().ListEnvironments(context.Background(), key)
						if err != nil {
							return err
						}
						for _, e := range envs {
							if e.Key == "prod" {
								return fmt.Errorf("environment prod was not deleted")
							}
						}
						return nil
					},
				),
			},
			{
				ResourceName:                         name,
				ImportState:                          true,
				ImportStateId:                        key,
				ImportStateVerify:                    true,
				ImportStateVerifyIdentifierAttribute: "key",
			},
		},
	})
}
