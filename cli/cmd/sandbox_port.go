// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"io"
	"os"
	"strconv"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/output"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
	"github.com/daytona/clients/cli/internal/workload"
	"golang.org/x/term"
)

func newSandboxPortSubcommand() *cobra.Command {
	cmd := &cobra.Command{Use: "port", Short: "Inspect sandbox ports and mint preview URLs"}
	cmd.AddCommand(newSandboxPreviewURLCommand())
	cmd.AddCommand(&cobra.Command{
		Use:   "list <sandbox-id>",
		Short: "List listening ports inside a sandbox",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			toolClient, _, _, err := sandboxToolboxClient(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			ports, err := toolClient.ListPorts(cmd.Context())
			if err != nil {
				return err
			}
			items := make([]int, 0, len(ports))
			for _, port := range ports {
				items = append(items, int(port))
			}
			return output.ListOut(a.Printer(), output.List[int]{Items: items}, func(items []int) output.Table {
				rows := make([][]string, 0, len(items))
				for _, item := range items {
					rows = append(rows, []string{strconv.Itoa(item)})
				}
				return output.Table{Headers: []string{"PORT"}, Rows: rows}
			})
		},
	})
	return cmd
}

// sandboxToolboxClient resolves the sandbox with the API credential, then
// uses the login's project key for toolbox requests.
func sandboxToolboxClient(ctx context.Context, a *app.App, sandboxID string) (*toolboxclient.Client, *toolboxclient.Target, *api.Client, error) {
	toolClient, target, apiClient, _, err := sandboxClients(ctx, a, sandboxID)
	return toolClient, target, apiClient, err
}

// sandboxClients is sandboxToolboxClient plus the SIGNING client — the one
// that mints pre-signed file URLs through the API under the same
// credential rule as toolbox requests (app.SigningClient): `sandbox cp`
// uses the same project key for signing and transfer requests.
func sandboxClients(ctx context.Context, a *app.App, sandboxID string) (*toolboxclient.Client, *toolboxclient.Target, *api.Client, *api.Client, error) {
	home, project, err := a.WorkloadClient(ctx, pickProject(a))
	if err != nil {
		return nil, nil, nil, nil, err
	}
	resolved, err := workload.ResolveSandbox(ctx, home, workload.NewCatalogCache(), sandboxID)
	if err != nil {
		return nil, nil, nil, nil, err
	}
	apiClient := resolved.Client
	target, err := toolboxclient.TargetFromDetail(resolved.Detail)
	if err != nil {
		return nil, nil, nil, nil, err
	}
	toolboxAPI, err := a.ToolboxClient(ctx, apiClient, target.ToolboxBaseURL, project)
	if err != nil {
		return nil, nil, nil, nil, err
	}
	signer, err := a.SigningClient(ctx, apiClient, project)
	if err != nil {
		return nil, nil, nil, nil, err
	}
	return toolboxclient.New(toolboxAPI), target, apiClient, signer, nil
}

func sandboxTimeout(a *app.App) (time.Duration, error) {
	return workload.ParseTimeout(a.Globals.Timeout)
}

// processStdinReader returns the CLI's stdin when it is not a terminal: a
// piped stdin is the process's input.
func processStdinReader() io.Reader {
	if term.IsTerminal(int(os.Stdin.Fd())) {
		return nil
	}
	return os.Stdin
}

// exitError maps a process's end to the CLI's exit status (the process
// exit code passes through); its error source is PROCESS.
func exitError(result tbgen.ExitResult) error {
	err := processExitError(result)
	var ce *cerr.Error
	if errors.As(err, &ce) {
		ce.Source = cerr.SourceProcess
	}
	return err
}

func processExitError(result tbgen.ExitResult) error {
	if result.Reason == nil {
		return exitCodeError(result)
	}
	switch *result.Reason {
	case tbgen.ReasonExited:
		if result.ExitCode == nil || *result.ExitCode == 0 {
			return nil
		}
		return cerr.New(int(*result.ExitCode), "process exited with code %d", *result.ExitCode)
	case tbgen.ReasonSignaled:
		if result.Signal != nil && *result.Signal == "INT" {
			return cerr.New(cerr.ExitInterrupted, "process interrupted")
		}
		if result.Signal != nil {
			return cerr.New(cerr.ExitFailure, "process terminated by signal %s", *result.Signal)
		}
		return cerr.New(cerr.ExitFailure, "process terminated by signal")
	case tbgen.ReasonTimedOut:
		return cerr.New(cerr.ExitTimeout, "timed out waiting for the process to finish")
	case tbgen.ReasonLost, tbgen.ReasonFailed:
		if result.ExitCode != nil && *result.ExitCode != 0 {
			return cerr.New(int(*result.ExitCode), "process %s with code %d", *result.Reason, *result.ExitCode)
		}
		return cerr.New(cerr.ExitFailure, "process %s", *result.Reason)
	default:
		return exitCodeError(result)
	}
}

// exitCodeError reports a nonzero exit code when the reason is absent or
// unknown: `reason` is optional on the wire, the exit code still decides.
func exitCodeError(result tbgen.ExitResult) error {
	if result.ExitCode == nil || *result.ExitCode == 0 {
		return nil
	}
	return cerr.New(int(*result.ExitCode), "process exited with code %d", *result.ExitCode)
}

func stdinModePtr(v tbgen.StdinMode) *tbgen.StdinMode { return &v }

func logEncodingOrDefault(value string) string {
	if value == "" {
		return "text"
	}
	return value
}
