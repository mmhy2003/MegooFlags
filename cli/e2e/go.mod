module github.com/mmhy2003/MegooFlags/cli/e2e

go 1.26.0

require (
	github.com/mmhy2003/MegooFlags/cli v0.0.0
	github.com/mmhy2003/MegooFlags/sdks/go v0.0.0
	github.com/open-feature/go-sdk v1.20.0
	github.com/santhosh-tekuri/jsonschema/v6 v6.0.3
)

require (
	go.uber.org/mock v0.6.0 // indirect
	golang.org/x/text v0.43.0 // indirect
)

replace (
	github.com/mmhy2003/MegooFlags/cli => ..
	github.com/mmhy2003/MegooFlags/sdks/go => ../../sdks/go
)
