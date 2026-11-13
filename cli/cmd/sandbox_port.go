// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
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
	cmd := &cobra.Command{Use: "port", Short: "Inspect sandbox ports, expose them and mint preview URLs"}
	cmd.AddCommand(newSandboxPreviewURLCommand())
	cmd.AddCommand(newSandboxPortExposeCommand())
	cmd.AddCommand(newSandboxPortUnexposeCommand())
	cmd.AddCommand(newSandboxPortEndpointsCommand())
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

// signalNumbers contains only numbers shared by Linux and macOS. Without
// a process OS at every exit-mapping call site, other signals must exit 1.
var signalNumbers = map[string]int{
	"HUP": 1, "INT": 2, "QUIT": 3, "ILL": 4, "TRAP": 5, "ABRT": 6, "FPE": 8,
	"KILL": 9, "SEGV": 11, "PIPE": 13, "ALRM": 14, "TERM": 15,
	"XCPU": 24, "XFSZ": 25, "VTALRM": 26, "PROF": 27, "WINCH": 28,
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
		if result.Signal == nil {
			return cerr.New(cerr.ExitFailure, "process terminated by signal")
		}
		return signalExitError(*result.Signal)
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

func signalExitError(signal string) error {
	number, ok := signalNumbers[strings.TrimPrefix(strings.ToUpper(signal), "SIG")]
	switch {
	case ok && number == 2:
		return cerr.New(cerr.ExitInterrupted, "process interrupted")
	case ok:
		return cerr.New(128+number, "process terminated by signal %s", signal)
	}
	return cerr.New(cerr.ExitFailure, "process terminated by signal %s", signal)
}

// exitCodeError reports the result when the reason is absent or unknown:
// `reason` is optional on the wire, so a reported signal, then the exit code,
// decides.
func exitCodeError(result tbgen.ExitResult) error {
	if result.Signal != nil && strings.TrimSpace(*result.Signal) != "" {
		return signalExitError(*result.Signal)
	}
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

func newSandboxPortExposeCommand() *cobra.Command {
	public := false
	cmd := &cobra.Command{
		Use:   "expose <sandbox-id> <port>",
		Short: "Expose a sandbox port as a preview URL, privately or publicly",
		Long:  "Expose a sandbox port as a preview URL (PUT /sandboxes/{id}/ports/{port}). The SDK twin is sandbox.ports.expose(port, public=…).\nPorts are private by default: a private URL needs the token from `daytona sandbox port preview-url` in the X-Daytona-Preview-Token header, or a short-lived `preview-url --signed` link. With --public the URL needs no credential and anyone who has it can open it, until the port is made private again: exposing an exposed port again sets its visibility, so running it without --public makes a public port private. `daytona sandbox port endpoints <sandbox-id>` shows each exposed port's visibility. The sandbox daemon's ports 2280, 2281 and 22220 are never preview ports and are refused.\nExamples:\n  daytona sandbox port expose <sandbox-id> 8080 --public\n  daytona sandbox port expose <sandbox-id> 8080",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			port, err := parsePreviewPortArg(args[1])
			if err != nil {
				return err
			}
			a := FromContext(cmd.Context())
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			resolved, err := workload.ResolveSandbox(cmd.Context(), home, workload.NewCatalogCache(), args[0])
			if err != nil {
				return err
			}
			path := fmt.Sprintf("/sandboxes/%s/ports/%d", url.PathEscape(resolved.Detail.Id), port)
			var resp api.Raw[gen.SandboxPort]
			if err := resolved.Client.Do(cmd.Context(), http.MethodPut, path, nil, gen.SetSandboxPortRequest{Public: public}, &resp); err != nil {
				return err
			}
			return a.Printer().Object(resp, [][2]string{{"port", strconv.Itoa(int(resp.V.Port))}, {"public", strconv.FormatBool(resp.V.Public)}, {"url", resp.V.Url}})
		},
	}
	cmd.Flags().BoolVar(&public, "public", false, "make the preview URL reachable without any credential")
	return cmd
}

func newSandboxPortEndpointsCommand() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{
		Use:   "endpoints <sandbox-id>",
		Short: "List a sandbox's exposed ports with their preview URLs and visibility",
		Long:  "List a sandbox's exposed ports (GET /sandboxes/{id}/ports): each one's preview URL and whether it is public. The SDK twin is sandbox.ports.list_endpoints(). `sandbox port list` instead lists the ports listening inside the sandbox.\nExample:\n  daytona sandbox port endpoints <sandbox-id>",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			resolved, err := workload.ResolveSandbox(cmd.Context(), home, workload.NewCatalogCache(), args[0])
			if err != nil {
				return err
			}
			path := fmt.Sprintf("/sandboxes/%s/ports", url.PathEscape(resolved.Detail.Id))
			fetch := func(ctx context.Context, cursor string) (output.List[api.Raw[gen.SandboxPort]], error) {
				return getRawList[gen.SandboxPort](ctx, resolved.Client, path, listQuery(flags, cursor), "ports")
			}
			var list output.List[api.Raw[gen.SandboxPort]]
			if flags.all {
				list, err = collectPaged(cmd.Context(), flags.cursor, fetch)
			} else {
				list, err = fetch(cmd.Context(), flags.cursor)
			}
			if err != nil {
				return err
			}
			return listOutRaw(a.Printer(), list, func(items []gen.SandboxPort) output.Table {
				rows := make([][]string, 0, len(items))
				for _, item := range items {
					rows = append(rows, []string{strconv.Itoa(int(item.Port)), strconv.FormatBool(item.Public), item.Url})
				}
				return output.Table{Headers: []string{"PORT", "PUBLIC", "URL"}, Rows: rows}
			})
		},
	}
	addPageFlags(cmd, &flags)
	return cmd
}

func newSandboxPortUnexposeCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "unexpose <sandbox-id> <port>",
		Short: "Remove a sandbox port's preview URL",
		Long:  "Remove a sandbox port's preview URL (DELETE /sandboxes/{id}/ports/{port}); it also works while the sandbox is being deleted. The SDK twin is sandbox.ports.unexpose(port).\nExample:\n  daytona sandbox port unexpose <sandbox-id> 8080",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			port, err := parsePortArg(args[1])
			if err != nil {
				return err
			}
			a := FromContext(cmd.Context())
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			resolved, err := workload.ResolveSandbox(cmd.Context(), home, workload.NewCatalogCache(), args[0])
			if err != nil {
				return err
			}
			path := fmt.Sprintf("/sandboxes/%s/ports/%d", url.PathEscape(resolved.Detail.Id), port)
			if err := resolved.Client.Do(cmd.Context(), http.MethodDelete, path, nil, nil, nil); err != nil {
				return err
			}
			return a.Printer().Raw("unexposed", map[string]any{"sandbox_id": resolved.Detail.Id, "port": port, "unexposed": true})
		},
	}
}
