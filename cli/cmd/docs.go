// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"
	"os"
	"strings"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/spf13/cobra"
	"github.com/spf13/cobra/doc"
)

func init() {
	Register(func() *cobra.Command {
		var full bool
		var dir string
		c := &cobra.Command{
			Use:   "docs",
			Short: "Print the command reference (--full: every command, one page)",
			Long: `Prints documentation for the CLI. With --full the entire command tree is
rendered as Markdown on stdout — one page an agent or a human can read in a
single pass. With --dir the same reference is written as one Markdown file per
command. --full and --dir cannot be combined.`,
			GroupID:     GroupOther,
			Args:        cobra.NoArgs,
			Annotations: map[string]string{annotationProfileOptional: "true"},
			RunE: func(cmd *cobra.Command, _ []string) error {
				if cmd.Flags().Changed("full") && cmd.Flags().Changed("dir") {
					return cerr.Usage("--full and --dir cannot be combined")
				}
				root := cmd.Root()
				root.DisableAutoGenTag = true
				if dir != "" {
					if err := os.MkdirAll(dir, 0o755); err != nil {
						return fmt.Errorf("create %s: %w", dir, err)
					}
					return doc.GenMarkdownTree(root, dir)
				}
				if !full {
					return cmd.Help()
				}
				var sb strings.Builder
				var walk func(c *cobra.Command) error
				walk = func(c *cobra.Command) error {
					if c.Hidden || c.Name() == "help" {
						return nil
					}
					if err := doc.GenMarkdown(c, &sb); err != nil {
						return err
					}
					sb.WriteString("\n---\n\n")
					for _, sub := range c.Commands() {
						if err := walk(sub); err != nil {
							return err
						}
					}
					return nil
				}
				if err := walk(root); err != nil {
					return err
				}
				_, err := fmt.Fprint(cmd.OutOrStdout(), sb.String())
				return err
			},
		}
		c.Flags().BoolVar(&full, "full", false, "render the whole command tree as one Markdown page")
		c.Flags().StringVar(&dir, "dir", "", "write one Markdown file per command into this directory")
		return c
	})
}
