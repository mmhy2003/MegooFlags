// Command terraform-provider-megooflags is the MegooFlags Terraform provider.
package main

import (
	"context"
	"flag"
	"log"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/mmhy2003/MegooFlags/terraform/internal/provider"
)

func main() {
	var debug bool
	flag.BoolVar(&debug, "debug", false, "run the provider with support for debuggers such as delve")
	flag.Parse()

	err := providerserver.Serve(context.Background(), provider.New(provider.Version), providerserver.ServeOpts{
		Address: "registry.terraform.io/mmhy2003/megooflags",
		Debug:   debug,
	})
	if err != nil {
		log.Fatal(err)
	}
}
