package provider

import (
	"context"
	"fmt"
	"regexp"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/terraform"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

// testAccCheckFlagEnvironmentOff checks that every flag_environment left
// in state is off with empty targeting. The project and its flags outlive
// Terraform, so the flag must still be there.
func testAccCheckFlagEnvironmentOff(s *terraform.State) error {
	ctx := context.Background()
	c := testAccClient()
	for name, rs := range s.RootModule().Resources {
		if rs.Type != "megooflags_flag_environment" {
			continue
		}
		a := rs.Primary.Attributes
		cfg, err := c.GetFlagConfig(ctx, a["project"], a["flag"], a["environment"])
		if err != nil {
			return fmt.Errorf("reading %s: %w", name, err)
		}
		if cfg.Enabled || len(cfg.Targets) > 0 || len(cfg.Rules) > 0 || len(cfg.Prerequisites) > 0 {
			return fmt.Errorf("%s was not reset: %+v", name, cfg)
		}
	}
	return nil
}

// testAccFlags creates the boolean flags of project through the API.
func testAccFlags(t *testing.T, project string, keys ...string) {
	t.Helper()
	for _, key := range keys {
		if _, err := testAccClient().CreateFlag(context.Background(), project, client.FlagCreate{
			Key: key, Name: key, Type: "boolean",
		}); err != nil {
			t.Fatalf("creating flag %s: %v", key, err)
		}
	}
}

func TestAccFlagEnvironment_basic(t *testing.T) {
	project := testAccKey(t)
	name := "megooflags_flag_environment.test"
	resource.Test(t, resource.TestCase{
		PreCheck: func() {
			testAccPreCheck(t)
			testAccProject(t, project)
			testAccFlags(t, project, "checkout", "login")
		},
		ProtoV6ProviderFactories: testAccProtoV6ProviderFactories,
		CheckDestroy:             testAccCheckFlagEnvironmentOff,
		Steps: []resource.TestStep{
			{
				Config: fmt.Sprintf(`
resource "megooflags_flag_environment" "test" {
  project     = %q
  flag        = "checkout"
  environment = "dev"
  enabled     = true
  targets = [
    { variation = "true", values = ["alice", "bob"] },
  ]
  rules = [
    {
      description = "Gold customers"
      clauses = [
        { attribute = "plan", operator = "in", values_json = jsonencode(["gold"]) },
        { attribute = "age", operator = "gte", values_json = jsonencode([18]), negate = true },
      ]
      variation = "true"
    },
  ]
  fallthrough = {
    rollout = {
      weights = [
        { variation = "true", weight = 25000 },
        { variation = "false", weight = 75000 },
      ]
    }
  }
}
`, project),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "enabled", "true"),
					resource.TestCheckResourceAttr(name, "targets.0.values.#", "2"),
					resource.TestCheckResourceAttr(name, "rules.0.clauses.#", "2"),
					resource.TestCheckResourceAttr(name, "rules.0.clauses.1.attribute", "age"),
					resource.TestCheckResourceAttr(name, "rules.0.clauses.1.negate", "true"),
					resource.TestCheckResourceAttr(name, "fallthrough.rollout.bucket_by", "key"),
					resource.TestCheckResourceAttr(name, "fallthrough.rollout.weights.0.weight", "25000"),
					resource.TestCheckResourceAttrSet(name, "version"),
				),
			},
			{
				Config: fmt.Sprintf(`
resource "megooflags_flag_environment" "test" {
  project     = %q
  flag        = "checkout"
  environment = "dev"
  enabled     = false
  rules = [
    {
      clauses = [
        { operator = "ends_with", values_json = jsonencode(["@example.com"]) },
      ]
      rollout = {
        bucket_by = "org"
        weights = [
          { variation = "true", weight = 50000 },
          { variation = "false", weight = 50000 },
        ]
      }
    },
  ]
  fallthrough = {
    variation = "false"
  }
  prerequisites = [
    { flag = "login", variation = "true" },
  ]
}
`, project),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "enabled", "false"),
					resource.TestCheckResourceAttr(name, "targets.#", "0"),
					resource.TestCheckResourceAttr(name, "rules.0.clauses.0.attribute", "key"),
					resource.TestCheckResourceAttr(name, "rules.0.rollout.bucket_by", "org"),
					resource.TestCheckResourceAttr(name, "fallthrough.variation", "false"),
					resource.TestCheckNoResourceAttr(name, "fallthrough.rollout"),
					resource.TestCheckResourceAttr(name, "prerequisites.0.flag", "login"),
				),
			},
			{
				ResourceName:                         name,
				ImportState:                          true,
				ImportStateId:                        project + "/checkout/dev",
				ImportStateVerify:                    true,
				ImportStateVerifyIdentifierAttribute: "flag",
			},
		},
	})
}

func TestAccFlagEnvironment_approvalRequired(t *testing.T) {
	project := testAccKey(t)
	resource.Test(t, resource.TestCase{
		PreCheck:                 func() { testAccPreCheck(t) },
		ProtoV6ProviderFactories: testAccProtoV6ProviderFactories,
		Steps: []resource.TestStep{
			{
				Config: fmt.Sprintf(`
resource "megooflags_project" "test" {
  key  = %q
  name = "Terraform acceptance"
  environments = [
    { key = "prod", name = "Production", approvals_required = 1 },
  ]
}

resource "megooflags_flag" "test" {
  project = megooflags_project.test.key
  key     = "checkout"
  type    = "boolean"
  name    = "Checkout"
}

resource "megooflags_flag_environment" "test" {
  project     = megooflags_project.test.key
  flag        = megooflags_flag.test.key
  environment = "prod"
  enabled     = true
  fallthrough = { variation = "true" }
}
`, project),
				ExpectError: regexp.MustCompile("needs approval"),
			},
		},
	})
}
