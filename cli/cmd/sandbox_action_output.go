// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"
	"net/http"
	"net/url"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

// printSandboxAfterAction prints fresh detail when available, otherwise the
// accepted operation. AsyncOperation and ForkSandboxOperation carry no tokens.
func printSandboxAfterAction(cmd *cobra.Command, resolved workload.ResolvedSandbox, ack any, id, verb string) error {
	a := FromContext(cmd.Context())
	if id == "" {
		id = resolved.Detail.Id
	}
	var detail api.Raw[gen.Sandbox]
	err := resolved.Client.Do(cmd.Context(), http.MethodGet, fmt.Sprintf("/sandboxes/%s", url.PathEscape(id)), nil, nil, &detail)
	if err != nil {
		if ack != nil {
			if verb != "delete" || cerr.ExitCode(err) != cerr.ExitNotFound {
				fmt.Fprintln(a.Stderr, output.SanitizeLine(fmt.Sprintf("warning: sandbox %s %s accepted, but its details could not be read; printing the acknowledgement", id, verb)))
			}
			return a.Printer().Object(ack, [][2]string{{"id", id}, {"action", verb}})
		}
		return err
	}
	return a.Printer().Object(detail, sandboxDetailKV(detail.V))
}
