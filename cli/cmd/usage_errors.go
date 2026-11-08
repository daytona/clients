// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"errors"

	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/cerr"
)

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
