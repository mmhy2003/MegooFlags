package provider

import (
	"errors"

	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

// diagFromError turns an error from action ("Creating project shop") into
// an error diagnostic. An approval_required error says how to get past it.
func diagFromError(err error, action string) diag.Diagnostic {
	summary := action + " failed"
	if client.IsCode(err, "approval_required") {
		return diag.NewErrorDiagnostic(summary, action+" needs approval in this environment. "+
			"Set approvals_required to 0 for environments Terraform manages, "+
			"or make this change through a change request outside Terraform.")
	}
	var apiErr *client.Error
	if errors.As(err, &apiErr) {
		return diag.NewErrorDiagnostic(summary, apiErr.Code+": "+apiErr.Message)
	}
	return diag.NewErrorDiagnostic(summary, err.Error())
}
