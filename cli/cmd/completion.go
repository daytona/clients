// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"os"
	"slices"
	"strings"

	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/config"
)

// Shell completion is local only: it offers the values a flag or an argument
// accepts and the saved profile names, and never sends a request (resource
// ids and regions are not completed). Every other positional argument names
// a remote resource, so the shell's file-name fallback is switched off for
// it; `sandbox cp` keeps it for its local endpoint.

// flagValueCompletion lists the values one flag accepts on the commands it
// names (by command path; "" is the root's persistent flag).
type flagValueCompletion struct {
	paths  []string
	flag   string
	values []string
}

var (
	signalNames          = []string{"TERM", "KILL", "INT", "HUP", "QUIT", "USR1", "USR2"}
	linuxTimeoutActions  = []string{"delete", "stop", "pause"}
	booleanValues        = []string{"true", "false"}
	sandboxSearchStatus  = []string{"queued", "pending", "assigned", "booting", "running", "pausing", "paused", "stopping", "stopped", "deleting", "deleted", "failed", "lost"}
	profileArgumentPaths = []string{"daytona profile use", "daytona profile delete"}
)

var flagValueCompletions = []flagValueCompletion{
	{paths: []string{""}, flag: "output", values: []string{"table", "json", "yaml"}},
	{paths: []string{"daytona sandbox wait"}, flag: "for", values: []string{"running", "stopped", "paused", "deleted", "started", "destroyed"}},
	{paths: []string{"daytona sandbox create", "daytona sandbox create-from-snapshot", "daytona sandbox update"}, flag: "on-idle-timeout", values: linuxTimeoutActions},
	{paths: []string{"daytona sandbox create", "daytona sandbox create-from-snapshot", "daytona sandbox update"}, flag: "on-lifecycle-timeout", values: linuxTimeoutActions},
	{paths: []string{"daytona sandbox create-macos"}, flag: "on-idle-timeout", values: []string{"delete", "stop"}},
	{paths: []string{"daytona sandbox create-macos"}, flag: "on-lifecycle-timeout", values: []string{"delete", "stop"}},
	{paths: []string{"daytona sandbox search"}, flag: "status", values: sandboxSearchStatus},
	{paths: []string{"daytona sandbox search"}, flag: "class", values: []string{"linux", "macos"}},
	{paths: []string{"daytona sandbox process list"}, flag: "state", values: []string{"running", "terminal", "all"}},
	{paths: []string{"daytona sandbox process list"}, flag: "kind", values: []string{"exec", "pty", "code", "session"}},
	{paths: []string{"daytona sandbox process logs"}, flag: "encoding", values: []string{"text", "base64"}},
	{paths: []string{"daytona sandbox process signal"}, flag: "escalate-to", values: signalNames},
	{paths: []string{"daytona image list"}, flag: "platform", values: []string{"true", "false", "any"}},
	{paths: []string{"daytona image list"}, flag: "anonymous", values: booleanValues},
	{paths: []string{"daytona registry set"}, flag: "kind", values: []string{"basic", "ghcr", "gcr", "ecr", "ecr_role"}},
	{paths: []string{"daytona volume create"}, flag: "type", values: []string{"disk", "bucket"}},
}

// argValueCompletions lists, per command path, the values of each positional
// argument (by index) that has a fixed vocabulary.
var argValueCompletions = map[string][][]string{
	"daytona config get":             {{"confirm"}},
	"daytona config set":             {{"confirm"}, booleanValues},
	"daytona sandbox process signal": {nil, nil, signalNames},
	"daytona billing plan set":       {{"starter", "team", "enterprise"}},
}

// fileArgumentPaths keep the shell's file-name completion for their
// positionals: they name a local path.
var fileArgumentPaths = []string{"daytona sandbox cp"}

// installCompletions registers the completion functions on the built tree.
func installCompletions(root *cobra.Command) {
	for _, entry := range flagValueCompletions {
		for _, path := range entry.paths {
			cmd := root
			if path != "" {
				cmd = findCommandPath(root, path)
			}
			if cmd == nil {
				continue
			}
			_ = cmd.RegisterFlagCompletionFunc(entry.flag, staticCompletion(entry.values))
		}
	}
	_ = root.RegisterFlagCompletionFunc("profile", completeProfileNames)
	walkLeaves(root, func(c *cobra.Command) {
		if c.ValidArgsFunction != nil || len(c.ValidArgs) > 0 || slices.Contains(fileArgumentPaths, c.CommandPath()) {
			return
		}
		path := c.CommandPath()
		switch {
		case slices.Contains(profileArgumentPaths, path):
			c.ValidArgsFunction = func(cmd *cobra.Command, args []string, toComplete string) ([]cobra.Completion, cobra.ShellCompDirective) {
				if len(args) > 0 {
					return nil, cobra.ShellCompDirectiveNoFileComp
				}
				return completeProfileNames(cmd, args, toComplete)
			}
		case argValueCompletions[path] != nil:
			byIndex := argValueCompletions[path]
			c.ValidArgsFunction = func(_ *cobra.Command, args []string, toComplete string) ([]cobra.Completion, cobra.ShellCompDirective) {
				if len(args) < len(byIndex) {
					return prefixed(byIndex[len(args)], toComplete), cobra.ShellCompDirectiveNoFileComp
				}
				return nil, cobra.ShellCompDirectiveNoFileComp
			}
		default:
			c.ValidArgsFunction = cobra.NoFileCompletions
		}
	})
}

func walkLeaves(c *cobra.Command, visit func(*cobra.Command)) {
	for _, sub := range c.Commands() {
		walkLeaves(sub, visit)
	}
	if c.Runnable() && !c.HasSubCommands() {
		visit(c)
	}
}

// findCommandPath returns the command whose CommandPath is path, or nil.
func findCommandPath(root *cobra.Command, path string) *cobra.Command {
	fields := strings.Fields(path)
	if len(fields) == 0 || fields[0] != root.Name() {
		return nil
	}
	cmd := root
	for _, name := range fields[1:] {
		var next *cobra.Command
		for _, sub := range cmd.Commands() {
			if sub.Name() == name {
				next = sub
				break
			}
		}
		if next == nil {
			return nil
		}
		cmd = next
	}
	return cmd
}

func staticCompletion(values []string) cobra.CompletionFunc {
	return func(_ *cobra.Command, _ []string, toComplete string) ([]cobra.Completion, cobra.ShellCompDirective) {
		return prefixed(values, toComplete), cobra.ShellCompDirectiveNoFileComp
	}
}

func prefixed(values []string, toComplete string) []cobra.Completion {
	out := make([]cobra.Completion, 0, len(values))
	for _, v := range values {
		if strings.HasPrefix(v, toComplete) {
			out = append(out, v)
		}
	}
	return out
}

// completeProfileNames offers the saved profile names. It reads config.yaml
// only (never the credentials file), creates nothing, and on any error offers
// nothing.
func completeProfileNames(_ *cobra.Command, _ []string, toComplete string) ([]cobra.Completion, cobra.ShellCompDirective) {
	dir, err := config.Dir(os.Getenv)
	if err != nil {
		return nil, cobra.ShellCompDirectiveNoFileComp
	}
	cfg, err := (&config.Store{Dir: dir}).LoadConfig()
	if err != nil {
		return nil, cobra.ShellCompDirectiveNoFileComp
	}
	names := make([]string, 0, len(cfg.Profiles))
	for _, p := range cfg.Profiles {
		names = append(names, p.Name)
	}
	return prefixed(names, toComplete), cobra.ShellCompDirectiveNoFileComp
}
