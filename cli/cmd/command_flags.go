// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"
	"regexp"

	"strings"

	"github.com/daytona/clients/cli/internal/cerr"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/workload"
)

// annotationCreatesProfile marks the command (login) whose --profile may
// name a profile that does not exist yet.
const annotationCreatesProfile = "daytona/creates-profile"

// annotationProfileOptional marks a command that does not act on the
// selected profile, so an unknown --profile / DAYTONA_PROFILE does not stop
// it (listing, switching or deleting profiles, settings, the reference).
const annotationProfileOptional = "daytona/profile-optional"

// profileOptional reports whether cmd runs without the selected profile:
// annotated commands and Cobra's own help and completion commands.
func profileOptional(cmd *cobra.Command) bool {
	for c := cmd; c != nil; c = c.Parent() {
		if c.Annotations[annotationProfileOptional] != "" {
			return true
		}
	}
	switch cmd.Name() {
	case "help", cobra.ShellCompRequestCmd, cobra.ShellCompNoDescRequestCmd:
		return true
	}
	return cmd.Parent() != nil && cmd.Parent().Name() == "completion" && cmd.Parent().Parent() == cmd.Root()
}

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
	if f := flags.Lookup("region"); f != nil {
		g.Region = f.Value.String()
	}
	if f := flags.Lookup("timeout"); f != nil {
		g.Timeout = f.Value.String()
	}
	g.APIKeySet = flags.Changed("api-key")
	g.ProfileMayBeNew = cmd.Annotations[annotationCreatesProfile] != "" && flags.Changed("profile")
	g.ProfileOptional = profileOptional(cmd)
	return g
}

// addRegionFlag registers --region on a command that places a resource in a
// region or lists by region. Only these commands accept it.
func addRegionFlag(cmd *cobra.Command, help string) {
	cmd.Flags().String("region", "", help)
	flagPlaceholder(cmd.Flags(), "region", "REGION")
}

// renamedFlags maps a flag spelling the 2.x CLI no longer accepts to its
// replacement, per command path.
var renamedFlags = map[string]map[string]string{
	"daytona volume create": {
		"region":  "--bucket-region (the S3 region of a customer bucket)",
		"size-gb": "--size-gib",
	},
	"daytona member remove": {"preview": "--dry-run"},
	"daytona sandbox create": {
		"cores-max": "--cpu-max", "mem-max": "--memory-max",
		"on-timeout": "--on-lifecycle-timeout (and --lifecycle-timeout for the lifetime)",
	},
	"daytona sandbox create-from-snapshot": {"on-timeout": "--on-lifecycle-timeout (and --lifecycle-timeout for the lifetime)"},
	"daytona sandbox create-macos":         {"on-timeout": "--on-lifecycle-timeout (and --lifecycle-timeout for the lifetime)"},
	"daytona sandbox update": {
		"on-timeout": "--on-lifecycle-timeout (and --lifecycle-timeout for the lifetime)",
		"timeout":    "--lifecycle-timeout DURATION (e.g. 1h) for the sandbox's lifetime",
	},
}

// lifecycleCommands took the sandbox lifetime as --timeout SECONDS before
// 2.x; their --timeout is now the CLI's own wait.
var lifecycleCommands = map[string]bool{
	"daytona sandbox create":               true,
	"daytona sandbox create-from-snapshot": true,
	"daytona sandbox create-macos":         true,
}

var bareTimeoutValue = regexp.MustCompile(`^invalid argument "[+-]?[0-9]+" for "--timeout" flag`)

// lifecycleTimeoutHint explains a bare-number --timeout on the commands
// where it used to be the sandbox lifetime in seconds.
func lifecycleTimeoutHint(cmd *cobra.Command, err error) string {
	if !lifecycleCommands[cmd.CommandPath()] || !bareTimeoutValue.MatchString(err.Error()) {
		return ""
	}
	return "the sandbox lifetime is now --lifecycle-timeout DURATION (e.g. 1h); --timeout is how long the CLI waits for the sandbox to start"
}

// renamedFlagHint names the replacement of a renamed flag in an
// "unknown flag" error, or returns "".
func renamedFlagHint(cmd *cobra.Command, err error) string {
	const prefix = "unknown flag: --"
	msg := err.Error()
	if !strings.HasPrefix(msg, prefix) {
		return ""
	}
	name := strings.TrimPrefix(msg, prefix)
	if next, ok := renamedFlags[cmd.CommandPath()][name]; ok {
		return "use " + next
	}
	if name == "no-wait" && !cmd.HasParent() {
		// A value-less flag unknown to the root hides the command name that
		// follows it, so cobra cannot find the command.
		return "put --no-wait after the command name (daytona sandbox stop ID --no-wait)"
	}
	if name == "region" {
		return "--region applies only to commands that place or list by region (sandbox create*, sandbox list and search, image list, create and build, reservation create, cpu-type and mac-model list)"
	}
	return ""
}

// checkFlagCombinations refuses a --timeout that would be accepted and
// ignored: with --no-wait, without --follow on a log command, and without
// --wait on `sandbox docker status`.
func checkFlagCombinations(cmd *cobra.Command) error {
	flags := cmd.Flags()
	if rule := flagCombinationRules[cmd.CommandPath()]; rule != nil {
		if err := rule(flags); err != nil {
			return err
		}
	}
	if !flags.Changed("timeout") {
		return nil
	}
	if flags.Changed("no-wait") {
		if noWait, _ := flags.GetBool("no-wait"); noWait {
			return cerr.Usage("--timeout has no effect with --no-wait").WithHint("drop one of them")
		}
	}
	for _, gate := range []string{"follow", "wait"} {
		f := flags.Lookup(gate)
		if f == nil || f.Value.Type() != "bool" {
			continue
		}
		if f.Value.String() != "true" {
			return cerr.Usage("--timeout requires --%s", gate)
		}
	}
	return nil
}

// flagCombinationRules refuse flag combinations a command would accept and
// ignore, per command path. They run before the configuration loads, so the
// mistake is a usage error whatever the profile.
var flagCombinationRules = map[string]func(*pflag.FlagSet) error{
	"daytona login": func(f *pflag.FlagSet) error {
		device, _ := f.GetBool("device")
		browser, _ := f.GetBool("browser")
		// The modes actually selected conflict, not the flags given:
		// `--device=$X --browser=$Y` from a script is fine while at most
		// one is true.
		if device && browser {
			return cerr.Usage("--device and --browser select different login flows; pass one")
		}
		if f.Changed("token") && (device || browser || f.Changed("client-id")) {
			return cerr.Usage("--token stores a static credential; it does not combine with --device, --browser or --client-id")
		}
		return nil
	},
	"daytona sandbox update": func(f *pflag.FlagSet) error {
		if clear, _ := f.GetBool("clear-labels"); clear && f.Changed("label") {
			return cerr.Usage("--clear-labels and --label both set the labels; pass one").
				WithHint("--label alone replaces the whole label set")
		}
		return nil
	},
	"daytona sandbox process signal": func(f *pflag.FlagSet) error {
		// The values decide, not the flags given: an empty --escalate-after
		// sets no delay, so the escalation would be sent and never happen.
		to, after := flagValue(f, "escalate-to"), flagValue(f, "escalate-after")
		if strings.TrimSpace(to) != "" && strings.TrimSpace(after) == "" {
			return cerr.Usage("--escalate-to requires --escalate-after")
		}
		return nil
	},
}

// runsWithoutConfig reports whether cmd still runs when the config cannot be
// loaded: annotated diagnostics (version, command groups) and the commands
// that never use it (help, docs, completion).
func runsWithoutConfig(cmd *cobra.Command) bool {
	if cmd.Annotations[annotationRunsWithoutConfig] != "" {
		return true
	}
	switch cmd.Name() {
	case "help", "docs", cobra.ShellCompRequestCmd, cobra.ShellCompNoDescRequestCmd:
		return cmd.Parent() == cmd.Root()
	}
	return cmd.Parent() != nil && cmd.Parent().Name() == "completion" && cmd.Parent().Parent() == cmd.Root()
}

// validateFlagRules runs cobra's required-flag and flag-group checks before
// anything else, as usage errors (exit 2): cobra runs them after the
// pre-run hooks and returns plain errors, which would exit 1.
func validateFlagRules(cmd *cobra.Command) error {
	if err := cmd.ValidateRequiredFlags(); err != nil {
		return cerr.Usage("%s", err.Error())
	}
	if err := cmd.ValidateFlagGroups(); err != nil {
		return cerr.Usage("%s", err.Error())
	}
	if f := cmd.Flags().Lookup("region"); f != nil && f.Changed && strings.TrimSpace(f.Value.String()) == "" {
		return cerr.Usage("--region is empty").WithHint("pass a region id (`daytona region list` shows them), or omit --region")
	}
	return nil
}

// flagValue is a flag's value as given, whatever its placeholder type.
func flagValue(f *pflag.FlagSet, name string) string {
	if flag := f.Lookup(name); flag != nil {
		return flag.Value.String()
	}
	return ""
}
