package megooflags_test

import (
	"context"
	"fmt"
	"log"

	"github.com/open-feature/go-sdk/openfeature"

	megooflags "github.com/mmhy2003/MegooFlags/sdks/go"
)

// Example mirrors the README quick start. A real service sets SDKKey from
// MEGOOFLAGS_SDK_KEY and BaseURL to its MegooFlags URL instead of Offline and
// a bootstrap file.
func Example() {
	err := openfeature.SetProviderAndWait(megooflags.NewProvider(megooflags.Config{
		SDKKey:        "mf_server_example",
		Offline:       true,
		BootstrapFile: "testdata/ruleset.json",
	}))
	if err != nil {
		log.Fatal(err)
	}
	defer openfeature.Shutdown()

	client := openfeature.NewDefaultClient()
	evalCtx := openfeature.NewEvaluationContext("user-123", map[string]any{"plan": "pro"})
	enabled, err := client.BooleanValue(context.Background(), "bool-flag", false, evalCtx)
	if err != nil {
		log.Print(err)
	}
	fmt.Println(enabled)
}
