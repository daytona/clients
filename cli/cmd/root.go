// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package cmd is the Cobra command tree. Root wires the global flags, the
// help groups (Workload / Management), and hands every subcommand an
// *app.App through the context.
package cmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"runtime"

	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
)

// Help group ids.
const (
	GroupAuth       = "auth"
	GroupWorkload   = "workload"
	GroupManagement = "management"
	GroupOther      = "other"
)

type ctxKey struct{}

type loadErrKey struct{}

// annotationRunsWithoutConfig marks a diagnostic command that still runs when
// the config cannot be loaded; LoadError returns the failure.
const annotationRunsWithoutConfig = "daytona/runs-without-config"

// LoadError returns why the root could not build the App for a command
// marked with annotationRunsWithoutConfig (nil when it could).
func LoadError(ctx context.Context) error {
	err, _ := ctx.Value(loadErrKey{}).(error)
	return err
}

// FromContext returns the App the root PersistentPreRunE installed.
func FromContext(ctx context.Context) *app.App {
	a, _ := ctx.Value(ctxKey{}).(*app.App)
	return a
}

// NewRoot builds the command tree. Global flags bind to a fresh Globals per
// root so tests (and embedders) never share flag state across roots.
func NewRoot(stdout, stderr io.Writer) *cobra.Command {
	globals := &app.Globals{}
	root := &cobra.Command{
		Use:   "daytona",
		Short: "Daytona — sandboxes, images, snapshots and your organization, from the terminal",
		Long: `daytona is the command line for the Daytona platform.

At a terminal it is interactive: tables, pickers and a confirmation before
destructive actions. When piped, redirected or run with --output json it is a
strict tool: no prompts, --yes required for destructive verbs, JSON on stdout,
diagnostics on stderr. Authorization is decided by the server; the CLI only
presents the credential you give it.

Get started:
  daytona login                     browser login (or --device on a remote host)
  daytona image create py312 --ref python:3.12   import a registry image once
  daytona sandbox create --image py312
  daytona sandbox exec <id> -- python -c 'print(1)'
  daytona docs --full               the whole reference in one page`,
		SilenceUsage:  true,
		SilenceErrors: true,
		Version:       app.Version,
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			a, err := app.Load(*globals, stdout, stderr, nil)
			if err != nil {
				if cmd.Annotations[annotationRunsWithoutConfig] != "" {
					cmd.SetContext(context.WithValue(cmd.Context(), loadErrKey{}, err))
					return nil
				}
				return err
			}
			installAuth(a)
			cmd.SetContext(context.WithValue(cmd.Context(), ctxKey{}, a))
			return nil
		},
	}
	root.SetOut(stdout)
	root.SetErr(stderr)
	// Cobra answers --version before any pre-run hook: no config, no network.
	root.Flags().BoolP("version", "v", false, "print the CLI version and exit")
	root.SetVersionTemplate("daytona {{.Version}} (" + runtime.GOOS + "/" + runtime.GOARCH + ")\n")
	// Unknown/malformed flags are usage errors (exit 2), like every other
	// argument mistake — each command registers only the flags its request
	// type has, so "unknown flag" is how a flag from another create surface
	// is reported.
	root.SetFlagErrorFunc(func(_ *cobra.Command, err error) error {
		return cerr.Usage("%s", err.Error())
	})

	pf := root.PersistentFlags()
	pf.StringVar(&globals.APIURL, "api-url", "", "API base URL, for test environments (env DAYTONA_V2_API_URL or DAYTONA_API_URL; default "+app.DefaultAPIURL+")")
	pf.StringVar(&globals.APIKey, "api-key", "", "static credential sent as-is; disables the saved login for this call (env DAYTONA_API_KEY)")
	pf.StringVar(&globals.Profile, "profile", "", "config profile to use (env DAYTONA_PROFILE)")
	pf.StringVar(&globals.Org, "org", "", "organization id or name for this call (env DAYTONA_ORG)")
	pf.StringVar(&globals.Project, "project", "", "project id or name for this call (env DAYTONA_PROJECT)")
	pf.StringVar(&globals.Region, "region", "", "region slug; filters lists and places creates (env DAYTONA_REGION)")
	pf.StringVarP(&globals.Output, "output", "o", "table", "output format: table|json|yaml")
	pf.BoolVarP(&globals.Yes, "yes", "y", false, "skip confirmation prompts (env DAYTONA_YES=1)")
	pf.BoolVar(&globals.NoWait, "no-wait", false, "return once the server accepts the operation instead of waiting for it to finish")
	pf.StringVar(&globals.Timeout, "timeout", "10m", "how long to wait for an operation to finish")
	pf.StringVarP(&globals.File, "file", "f", "", "JSON request body from a file (or - for stdin); explicit flags override its fields")
	pf.BoolVarP(&globals.Quiet, "quiet", "q", false, "suppress progress and informational output on stderr")
	pf.BoolVar(&globals.Debug, "debug", false, "log every request to stderr")

	root.AddGroup(
		&cobra.Group{ID: GroupAuth, Title: "Login and context:"},
		&cobra.Group{ID: GroupWorkload, Title: "Sandboxes and resources:"},
		&cobra.Group{ID: GroupManagement, Title: "Organization management:"},
		&cobra.Group{ID: GroupOther, Title: "Other:"},
	)
	root.SetHelpCommandGroupID(GroupOther)
	root.SetCompletionCommandGroupID(GroupOther)

	for _, reg := range registry {
		root.AddCommand(reg())
	}
	return root
}

// registry collects subcommand constructors; each family's init() appends.
var registry []func() *cobra.Command

// Register adds a family constructor.
func Register(f func() *cobra.Command) { registry = append(registry, f) }

// installAuth is set by the auth command file so app gets the profile
// credential resolver without an import cycle.
var installAuth = func(*app.App) {}

// Execute runs the tree and maps the error to an exit code.
func Execute() int {
	root := NewRoot(os.Stdout, os.Stderr)
	ctx, cancel := signalContext()
	defer cancel()
	err := root.ExecuteContext(ctx)
	if err == nil {
		return 0
	}
	printError(os.Stderr, err)
	return cerr.ExitCode(err)
}

func printError(w io.Writer, err error) {
	// Error text embeds server-supplied strings (error envelopes, names);
	// sanitize so a hostile message cannot drive the user's terminal.
	var ce *cerr.Error
	if errors.As(err, &ce) {
		fmt.Fprintf(w, "error: %s\n", output.Sanitize(ce.Error()))
		if ce.Hint != "" {
			fmt.Fprintf(w, "hint: %s\n", output.Sanitize(ce.Hint))
		}
		return
	}
	fmt.Fprintf(w, "error: %s\n", output.Sanitize(err.Error()))
}
