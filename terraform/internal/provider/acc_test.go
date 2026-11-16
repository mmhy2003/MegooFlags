package provider

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"os"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
	"github.com/hashicorp/terraform-plugin-testing/terraform"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

// The acceptance tests (TestAcc*) run Terraform against a running stack.
// They need TF_ACC, MEGOOFLAGS_E2E_URL (for example http://localhost:8080)
// and MEGOOFLAGS_API_TOKEN (an org admin token), and skip otherwise.

// testAccProtoV6ProviderFactories serves the provider in process.
var testAccProtoV6ProviderFactories = map[string]func() (tfprotov6.ProviderServer, error){
	"megooflags": providerserver.NewProtocol6WithError(New("test")()),
}

// testAccPreCheck skips the test without a stack and points the provider
// at it through MEGOOFLAGS_URL.
func testAccPreCheck(t *testing.T) {
	t.Helper()
	if os.Getenv("TF_ACC") == "" || os.Getenv("MEGOOFLAGS_E2E_URL") == "" || os.Getenv("MEGOOFLAGS_API_TOKEN") == "" {
		t.Skip("TF_ACC, MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN must be set")
	}
	t.Setenv("MEGOOFLAGS_URL", os.Getenv("MEGOOFLAGS_E2E_URL"))
}

// testAccClient is an API client for the stack, for setup and checks.
func testAccClient() *client.Client {
	return client.New(os.Getenv("MEGOOFLAGS_E2E_URL"), os.Getenv("MEGOOFLAGS_API_TOKEN"))
}

// testAccKey returns a random key, tfacc-<hex>.
func testAccKey(t *testing.T) string {
	t.Helper()
	b := make([]byte, 5)
	if _, err := rand.Read(b); err != nil {
		t.Fatalf("random key: %v", err)
	}
	return "tfacc-" + hex.EncodeToString(b)
}

// testAccProject creates project key with the environments dev and prod
// through the API, for resources that need a project Terraform does not
// manage. It is deleted when the test ends.
func testAccProject(t *testing.T, key string) {
	t.Helper()
	ctx := context.Background()
	c := testAccClient()
	_, err := c.CreateProject(ctx, client.ProjectCreate{
		Key:  key,
		Name: "Terraform acceptance " + key,
		Environments: []client.EnvironmentCreate{
			{Key: "dev", Name: "Development"},
			{Key: "prod", Name: "Production"},
		},
	})
	if err != nil {
		t.Fatalf("creating project %s: %v", key, err)
	}
	t.Cleanup(func() {
		if err := c.DeleteProject(context.Background(), key); err != nil && !client.IsNotFound(err) {
			t.Errorf("deleting project %s: %v", key, err)
		}
	})
}

// testAccCheckGone returns a CheckDestroy that fails while get finds any
// resource of type typ left in state.
func testAccCheckGone(typ string, get func(ctx context.Context, c *client.Client, attrs map[string]string) error) func(*terraform.State) error {
	return func(s *terraform.State) error {
		ctx := context.Background()
		c := testAccClient()
		for name, rs := range s.RootModule().Resources {
			if rs.Type != typ {
				continue
			}
			err := get(ctx, c, rs.Primary.Attributes)
			if err == nil {
				return fmt.Errorf("%s still exists", name)
			}
			if !client.IsNotFound(err) {
				return fmt.Errorf("checking %s: %w", name, err)
			}
		}
		return nil
	}
}

// testAccImportID returns an ImportStateIdFunc that joins the attributes
// of resource name with "/".
func testAccImportID(name string, attrs ...string) func(*terraform.State) (string, error) {
	return func(s *terraform.State) (string, error) {
		rs, ok := s.RootModule().Resources[name]
		if !ok {
			return "", fmt.Errorf("%s not in state", name)
		}
		id := ""
		for i, a := range attrs {
			if i > 0 {
				id += "/"
			}
			id += rs.Primary.Attributes[a]
		}
		return id, nil
	}
}
