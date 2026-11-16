package provider

import (
	"context"
	"fmt"
	"regexp"
	"testing"

	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
	"github.com/hashicorp/terraform-plugin-testing/terraform"
)

// regexpServerKey matches the secret of a server key.
var regexpServerKey = regexp.MustCompile(`^mf_server_`)

// testAccCheckSdkKeyRevoked checks that the key with *id in project's dev
// environment is revoked (or gone).
func testAccCheckSdkKeyRevoked(project string, id *string) func(*terraform.State) error {
	return func(*terraform.State) error {
		keys, err := testAccClient().ListSdkKeys(context.Background(), project, "dev")
		if err != nil {
			return err
		}
		if k := findActiveSdkKey(keys, *id); k != nil {
			return fmt.Errorf("SDK key %s is not revoked", *id)
		}
		return nil
	}
}

// testAccSaveID stores attribute id of resource name in *id.
func testAccSaveID(name string, id *string) func(*terraform.State) error {
	return func(s *terraform.State) error {
		rs, ok := s.RootModule().Resources[name]
		if !ok {
			return fmt.Errorf("%s not in state", name)
		}
		*id = rs.Primary.Attributes["id"]
		return nil
	}
}

func TestAccSdkKey_basic(t *testing.T) {
	project := testAccKey(t)
	name := "megooflags_sdk_key.test"
	var first, second string
	config := func(keyName, kind string) string {
		return fmt.Sprintf(`
resource "megooflags_sdk_key" "test" {
  project     = %q
  environment = "dev"
  name        = %q
  kind        = %q
}
`, project, keyName, kind)
	}
	resource.Test(t, resource.TestCase{
		// The project outlives Terraform, so destroy can be checked as a
		// revocation.
		PreCheck: func() {
			testAccPreCheck(t)
			testAccProject(t, project)
		},
		ProtoV6ProviderFactories: testAccProtoV6ProviderFactories,
		CheckDestroy:             testAccCheckSdkKeyRevoked(project, &second),
		Steps: []resource.TestStep{
			{
				Config: config("backend", "server"),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "kind", "server"),
					resource.TestCheckResourceAttrSet(name, "id"),
					resource.TestCheckResourceAttrSet(name, "prefix"),
					resource.TestCheckResourceAttrSet(name, "created_at"),
					resource.TestMatchResourceAttr(name, "secret", regexpServerKey),
					testAccSaveID(name, &first),
				),
			},
			{
				// Every argument replaces the key: the old one is revoked.
				Config: config("browser", "client"),
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr(name, "name", "browser"),
					resource.TestCheckResourceAttr(name, "kind", "client"),
					resource.TestCheckResourceAttrSet(name, "secret"),
					testAccSaveID(name, &second),
					func(s *terraform.State) error {
						if first == second {
							return fmt.Errorf("the key was not replaced")
						}
						return testAccCheckSdkKeyRevoked(project, &first)(s)
					},
				),
			},
			{
				ResourceName:            name,
				ImportState:             true,
				ImportStateIdFunc:       testAccImportID(name, "project", "environment", "id"),
				ImportStateVerify:       true,
				ImportStateVerifyIgnore: []string{"secret"},
			},
		},
	})
}
