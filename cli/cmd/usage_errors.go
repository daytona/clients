// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"errors"
	"strconv"
	"strings"

	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/cerr"
)

// usageArgs wraps a command's positional-argument validator so that every
// arity mistake is a usage error (exit 2), and so that a blank argument in a
// position the command's Use line names is one too (see refuseBlankArgs).
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
		return refuseBlankArgs(cmd, args)
	}
}

// annotationFreeTextFrom marks a command whose positionals from this index
// on are free text a blank value is meaningful for, or that the command
// checks itself (`sandbox search [query]`, `config set <key> <value>`).
const annotationFreeTextFrom = "daytona/free-text-from"

// refuseBlankArgs refuses an empty or whitespace-only argument given for a
// named positional (an id, a name, a host, a path): no command accepts one,
// and sent as given it would reach the API as a collection path or an
// unknown name. Not checked: positionals annotationFreeTextFrom marks, and
// the positions from the Use line's `--` or variadic placeholder on (ssh and
// exec arguments may be legitimately empty). A `--` on the command line only
// ends the options: `get -- ""` still gives the <id>, so it is checked.
func refuseBlankArgs(cmd *cobra.Command, args []string) error {
	placeholders := namedPositionals(cmd)
	for i, arg := range args {
		if i >= len(placeholders) {
			break
		}
		if strings.TrimSpace(arg) == "" {
			return cerr.Usage("argument %s is empty", placeholders[i]).WithHint("run `%s --help` for usage", cmd.CommandPath())
		}
	}
	return nil
}

// namedPositionals returns the placeholders refuseBlankArgs checks: the
// positionals of cmd's Use line (usePositionals) before the index
// annotationFreeTextFrom names.
func namedPositionals(cmd *cobra.Command) []string {
	out := usePositionals(cmd.Use)
	if from, err := strconv.Atoi(cmd.Annotations[annotationFreeTextFrom]); err == nil && from < len(out) {
		out = out[:from]
	}
	return out
}

// usePositionals reads the positional placeholders of a Use line in order
// ("get <id>" → ["<id>"], "use [id|name]" → ["<id|name>"]), stopping at the
// first flag, `--` or variadic placeholder; `[flags]` is skipped.
func usePositionals(use string) []string {
	fields := strings.Fields(use)
	if len(fields) < 2 {
		return nil
	}
	var out []string
	for _, field := range fields[1:] {
		switch {
		case field == "[flags]":
			continue
		case strings.HasPrefix(field, "-"), strings.Contains(field, "..."):
			return out
		case strings.HasPrefix(field, "[") && strings.HasSuffix(field, "]"):
			field = "<" + field[1:len(field)-1] + ">"
		}
		out = append(out, field)
	}
	return out
}

// rejectUnknownSubcommand is the argument validator of a command group: any
// positional argument is an unknown command (exit 2), never a silent help page.
func rejectUnknownSubcommand(cmd *cobra.Command, args []string) error {
	if len(args) == 0 {
		return nil
	}
	e := cerr.Usage("unknown command %q for %q", args[0], cmd.CommandPath())
	if next, ok := renamedCommands[cmd.CommandPath()+" "+args[0]]; ok {
		return e.WithHint("use `daytona %s`", next)
	}
	if suggestions := cmd.SuggestionsFor(args[0]); len(suggestions) > 0 {
		return e.WithHint("did you mean %q? Run `%s --help` for the available commands", suggestions[0], cmd.CommandPath())
	}
	return e.WithHint("run `%s --help` for the available commands", cmd.CommandPath())
}

// renamedCommands maps a command path the 2.x CLI no longer has to its
// replacement.
var renamedCommands = map[string]string{
	"daytona invite show":         "invite get",
	"daytona sandbox preview-url": "sandbox port preview-url",
	"daytona image import":        "image create NAME --ref REF",
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

// newHelpCommand is Cobra's help command, except that an unknown topic is a
// usage error (exit 2) like an unknown command.
func newHelpCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "help [command]",
		Short:   "Help about any command",
		Long:    "Help provides help for any command in the application.\nSimply type daytona help [path to command] for full details.",
		GroupID: GroupOther,
		ValidArgsFunction: func(c *cobra.Command, args []string, toComplete string) ([]cobra.Completion, cobra.ShellCompDirective) {
			cmd, _, err := c.Root().Find(args)
			if err != nil {
				return nil, cobra.ShellCompDirectiveNoFileComp
			}
			var completions []cobra.Completion
			for _, sub := range cmd.Commands() {
				if (sub.IsAvailableCommand() || sub.Name() == "help") && strings.HasPrefix(sub.Name(), toComplete) {
					completions = append(completions, cobra.CompletionWithDesc(sub.Name(), sub.Short))
				}
			}
			return completions, cobra.ShellCompDirectiveNoFileComp
		},
		RunE: func(c *cobra.Command, args []string) error {
			cmd, rest, err := c.Root().Find(args)
			if err != nil || len(rest) > 0 {
				return cerr.Usage("unknown help topic %q", strings.Join(args, " ")).
					WithHint("run `daytona --help` for the available commands")
			}
			cmd.InitDefaultHelpFlag()
			cmd.InitDefaultVersionFlag()
			return cmd.Help()
		},
	}
}
