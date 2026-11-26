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
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

func newSandboxForkCommand() *cobra.Command {
	name := ""
	cmd := newSandboxActionCommand("fork", func(cmd *cobra.Command, resolved workload.ResolvedSandbox) (any, string, string, error) {
		macos := resolved.Detail.Class == workload.ClassMacOS
		if resolved.Detail.Class != workload.ClassLinux && !macos {
			return nil, "", "", cerr.New(cerr.ExitConflict, "cannot fork sandbox %s with unknown class %s", resolved.Detail.Id, resolved.Detail.Class)
		}
		body := (*gen.ForkSandboxRequest)(nil)
		if cmd.Flags().Changed("name") {
			body = &gen.ForkSandboxRequest{Name: &name}
		}
		attempts := 0
		var resp api.Raw[gen.ForkSandboxOperation]
		if err := retrySandboxTransition(cmd.Context(), func() error {
			attempts++
			return resolved.Client.Do(cmd.Context(), http.MethodPost, fmt.Sprintf("/sandboxes/%s/fork", url.PathEscape(resolved.Detail.Id)), nil, body, &resp)
		}); err != nil {
			return nil, "", "", err
		}
		status := resolved.Detail.Status
		if attempts > 1 && !FromContext(cmd.Context()).Globals.NoWait {
			// A transition refusal invalidates the pre-loop source status.
			// Read once after acceptance, on the already resolved region client.
			var source gen.Sandbox
			if err := resolved.Client.Do(cmd.Context(), http.MethodGet, "/sandboxes/"+url.PathEscape(resolved.Detail.Id), nil, nil, &source); err != nil {
				return nil, "", "", createdButUnreadable(resp.V.Id, err)
			}
			status = source.Status
		}
		// Running sources bring the fork up; other sources leave Linux forks
		// paused and macOS forks stopped after disk preparation.
		waitFor := "paused"
		if status == "running" {
			waitFor = "running"
		} else if macos {
			waitFor = "stopped"
		}
		return resp, resp.V.Id, waitFor, nil
	})
	cmd.Flags().StringVar(&name, "name", "", "fork name")
	cmd.Long = "Fork a sandbox.\nA sandbox still starting, stopping or pausing is retried for up to 30 seconds before the fork is accepted.\nExample:\n  daytona sandbox fork <id>"
	return cmd
}
