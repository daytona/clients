// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package cmd is the Cobra command tree. Root wires the global flags, the
// help groups (Workload / Management), and hands every subcommand an
// *app.App through the context.
package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"runtime"
	"strings"

	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/workload"
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

// annotationCreatesProfile marks the command (login) whose --profile may
// name a profile that does not exist yet.
const annotationCreatesProfile = "daytona/creates-profile"

// defaultWaitTimeout is the --timeout default of commands that wait.
const defaultWaitTimeout = "10m"

// addFileFlag registers --file/-f on a command that takes a JSON request
// body. Only these commands accept it.
func addFileFlag(cmd *cobra.Command, help string) {
	if help == "" {
		help = "JSON request body from a file (or - for stdin); explicit flags override its fields"
	}
	cmd.Flags().StringP("file", "f", "", help)
	flagPlaceholder(cmd.Flags(), "file", "PATH")
}

// addWaitFlags registers --no-wait and --timeout on a command that waits for
// an operation to finish.
func addWaitFlags(cmd *cobra.Command) {
	cmd.Flags().Bool("no-wait", false, "return once the server accepts the operation instead of waiting for it to finish")
	addTimeoutFlag(cmd, defaultWaitTimeout, "how long to wait for the operation to finish (Go duration); the server keeps working after it")
}

// addTimeoutFlag registers --timeout DURATION, the command's local wait. The
// value is checked while flags are parsed: a Go duration greater than zero.
func addTimeoutFlag(cmd *cobra.Command, def, help string) {
	value := waitTimeoutFlag(def)
	cmd.Flags().Var(&value, "timeout", help)
	flagPlaceholder(cmd.Flags(), "timeout", "DURATION")
}

// waitTimeoutFlag is the value of --timeout.
type waitTimeoutFlag string

func (f *waitTimeoutFlag) String() string { return string(*f) }

func (f *waitTimeoutFlag) Type() string { return "duration" }

func (f *waitTimeoutFlag) Set(raw string) error {
	if _, err := workload.ParseTimeout(raw); err != nil {
		return fmt.Errorf("want a Go duration greater than zero, such as 30s or 10m")
	}
	*f = waitTimeoutFlag(raw)
	return nil
}

// commandGlobals completes the root's globals with what this command's
// parsed flags say: the per-command --file / --no-wait / --timeout, and
// whether --api-key was given at all.
func commandGlobals(cmd *cobra.Command, g app.Globals) app.Globals {
	flags := cmd.Flags()
	if f := flags.Lookup("file"); f != nil {
		g.File = f.Value.String()
	}
	if f := flags.Lookup("no-wait"); f != nil {
		g.NoWait = f.Value.String() == "true"
	}
	if f := flags.Lookup("timeout"); f != nil {
		g.Timeout = f.Value.String()
	}
	g.APIKeySet = flags.Changed("api-key")
	g.ProfileMayBeNew = cmd.Annotations[annotationCreatesProfile] != ""
	return g
}

// usageArgs wraps a command's positional-argument validator so that every
// arity mistake is a usage error (exit 2).
func usageArgs(validate cobra.PositionalArgs) cobra.PositionalArgs {
	return func(cmd *cobra.Command, args []string) error {
		if validate == nil {
			return nil
		}
		if err := validate(cmd, args); err != nil {
			var ce *cerr.Error
			if errors.As(err, &ce) {
				return err
			}
			return cerr.Usage("%s", err.Error()).WithHint("run `%s --help` for usage", cmd.CommandPath())
		}
		return nil
	}
}

// rejectUnknownSubcommand is the argument validator of a command group: any
// positional argument is an unknown command (exit 2), never a silent help page.
func rejectUnknownSubcommand(cmd *cobra.Command, args []string) error {
	if len(args) == 0 {
		return nil
	}
	e := cerr.Usage("unknown command %q for %q", args[0], cmd.CommandPath())
	if suggestions := cmd.SuggestionsFor(args[0]); len(suggestions) > 0 {
		return e.WithHint("did you mean %q? Run `%s --help` for the available commands", suggestions[0], cmd.CommandPath())
	}
	return e.WithHint("run `%s --help` for the available commands", cmd.CommandPath())
}

// normalizeTree makes every arity error, unknown command and unknown
// subcommand a usage error: leaves keep their validator, wrapped; groups
// (and the root) reject any positional argument and print their help when
// called bare.
func normalizeTree(c *cobra.Command) {
	for _, sub := range c.Commands() {
		normalizeTree(sub)
	}
	if c.Name() == "help" || c.Name() == cobra.ShellCompRequestCmd || c.Name() == cobra.ShellCompNoDescRequestCmd {
		return
	}
	if c.HasSubCommands() && !c.Runnable() {
		c.Args = rejectUnknownSubcommand
		c.RunE = func(cmd *cobra.Command, _ []string) error { return cmd.Help() }
		if c.Annotations == nil {
			c.Annotations = map[string]string{}
		}
		c.Annotations[annotationRunsWithoutConfig] = "help"
		return
	}
	if c.Args == nil {
		c.Args = cobra.ArbitraryArgs
	}
	c.Args = usageArgs(c.Args)
}

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
diagnostics on stderr; with --output json an error is one JSON object on
stderr. Authorization is decided by the server; the CLI only presents the
credential you give it.

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
			a, err := app.Load(commandGlobals(cmd, *globals), stdout, stderr, nil)
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
	pf.StringVar(&globals.APIURL, "api-url", "", "API base URL, for test environments (env DAYTONA_V2_API_URL; default "+app.DefaultAPIURL+")")
	pf.StringVar(&globals.APIKey, "api-key", "", "static credential sent as-is; disables the saved login for this call (env DAYTONA_API_KEY)")
	pf.StringVar(&globals.Profile, "profile", "", "saved profile to use; it must exist (env DAYTONA_PROFILE)")
	pf.StringVar(&globals.Org, "org", "", "organization id or name for this call (env DAYTONA_ORG)")
	pf.StringVar(&globals.Project, "project", "", "project id or name for this call (env DAYTONA_PROJECT)")
	pf.StringVar(&globals.Region, "region", "", "region slug; filters lists and places creates")
	pf.StringVarP(&globals.Output, "output", "o", "", "output format: table|json|yaml (default table at a terminal, json otherwise)")
	pf.BoolVarP(&globals.Yes, "yes", "y", false, "confirm destructive and billable actions; required off a terminal (env DAYTONA_YES=1)")
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
	root.InitDefaultHelpCmd()
	root.InitDefaultCompletionCmd()
	normalizeTree(root)
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
	reportError(root, os.Stdout, os.Stderr, err)
	return cerr.ExitCode(err)
}

// reportError prints err on stderr: one JSON object
// {"error": {"exit_code", "code", "message", "source", "trace_id", "hint"}}
// when the invocation's output format is JSON (explicitly, or by default off
// a terminal), prose otherwise.
func reportError(root *cobra.Command, stdout, stderr io.Writer, err error) {
	if errorOutputIsJSON(root, stdout) {
		printErrorJSON(stderr, err)
		return
	}
	printError(stderr, err)
}

func errorOutputIsJSON(root *cobra.Command, stdout io.Writer) bool {
	flag := root.PersistentFlags().Lookup("output")
	if flag == nil {
		return false
	}
	format, err := app.ResolveFormat(flag.Value.String(), app.WriterIsTerminal(stdout))
	return err == nil && format == output.FormatJSON
}

func printErrorJSON(w io.Writer, err error) {
	details := cerr.DetailsOf(err)
	details.Message = strings.TrimSpace(details.Message)
	_ = json.NewEncoder(w).Encode(struct {
		Error cerr.Details `json:"error"`
	}{details})
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
