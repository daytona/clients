// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"strconv"

	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
)

func init() {
	Register(func() *cobra.Command {
		c := &cobra.Command{Use: "config", Short: "Read and change CLI settings", GroupID: GroupOther, Annotations: map[string]string{annotationProfileOptional: "true"}}
		c.AddCommand(&cobra.Command{
			Use:   "get <key>",
			Short: "Print a setting (confirm)",
			Args:  cobra.ExactArgs(1),
			RunE: func(cmd *cobra.Command, args []string) error {
				a := FromContext(cmd.Context())
				switch args[0] {
				case "confirm":
					v := a.Config.Settings.ConfirmEnabled()
					return a.Printer().Raw(strconv.FormatBool(v), map[string]any{"confirm": v})
				}
				return cerr.Usage("unknown setting %q (known: confirm)", args[0])
			},
		})
		c.AddCommand(&cobra.Command{
			Use:   "set <key> <value>",
			Short: "Change a setting (confirm true|false — prompts before destructive verbs)",
			Long: "Change a setting.\n\n" +
				"confirm true|false: whether destructive and billable commands ask for confirmation at a terminal.\n" +
				"It affects prompts only: off a terminal (and with --output json or yaml) those commands always\n" +
				"need --yes or DAYTONA_YES=1.",
			Args: cobra.ExactArgs(2),
			RunE: func(cmd *cobra.Command, args []string) error {
				a := FromContext(cmd.Context())
				switch args[0] {
				case "confirm":
					v, err := strconv.ParseBool(args[1])
					if err != nil {
						return cerr.Usage("confirm wants true|false, got %q", args[1])
					}
					return a.Store.Update(func(c *config.Config, _ *config.Credentials) (bool, bool, error) {
						c.Settings.Confirm = &v
						return true, false, nil
					})
				}
				return cerr.Usage("unknown setting %q (known: confirm)", args[0])
			},
		})
		c.AddCommand(&cobra.Command{
			Use:   "path",
			Short: "Print the config directory",
			Args:  cobra.NoArgs,
			RunE: func(cmd *cobra.Command, _ []string) error {
				a := FromContext(cmd.Context())
				return a.Printer().Raw(a.Store.Dir, map[string]any{"path": a.Store.Dir})
			},
		})
		return c
	})
}
