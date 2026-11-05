// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/auth"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/daytona/clients/cli/internal/output"
)

func init() { Register(newProfileCmd) }

func newProfileCmd() *cobra.Command {
	c := &cobra.Command{
		Use:     "profile",
		Short:   "Manage named connections (API URL + credential + org/project context)",
		GroupID: GroupAuth,
	}
	c.AddCommand(&cobra.Command{
		Use:   "list",
		Short: "List profiles",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			type row struct {
				Name       string `json:"name"`
				APIURL     string `json:"api_url"`
				Credential string `json:"credential"`
				OrgID      string `json:"org_id,omitempty"`
				ProjectID  string `json:"project_id,omitempty"`
				Active     bool   `json:"active"`
			}
			l := output.List[row]{Items: []row{}}
			for _, p := range a.Config.Profiles {
				l.Items = append(l.Items, row{p.Name, p.APIURL, p.Credential, p.OrgID, p.ProjectID(), p.Name == a.Config.ActiveProfile})
			}
			return output.ListOut(a.Printer(), l, func(rs []row) output.Table {
				t := output.Table{Headers: []string{"", "NAME", "API URL", "CREDENTIAL", "ORG", "PROJECT"}}
				for _, r := range rs {
					mark := ""
					if r.Active {
						mark = "*"
					}
					t.Rows = append(t.Rows, []string{mark, r.Name, r.APIURL, r.Credential, r.OrgID, r.ProjectID})
				}
				return t
			})
		},
	})
	c.AddCommand(&cobra.Command{
		Use:   "use <name>",
		Short: "Make a profile the active one",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			return a.Store.Update(func(c *config.Config, _ *config.Credentials) (bool, bool, error) {
				if _, ok := c.Profile(args[0]); !ok {
					return false, false, cerr.New(cerr.ExitNotFound, "no profile %q", args[0]).WithHint("see `daytona profile list`; `daytona login --profile %s` creates it", args[0])
				}
				c.ActiveProfile = args[0]
				return true, false, nil
			})
		},
	})
	c.AddCommand(&cobra.Command{
		Use:   "delete <name>",
		Short: "Delete a profile and its saved credentials",
		Long: `Deletes the profile and its saved credentials from this machine, then attempts
to revoke the profile's cached login-minted project keys against the profile's
saved API, like logout (at most 3 seconds total). Local deletion completes even
when that API is unavailable; keys whose revocation fails expire server-side.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			if err := a.Confirm("delete", "profile "+args[0]); err != nil {
				return err
			}
			var cleanup auth.LoginKeyCleanup
			err := a.Store.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
				p, ok := c.Profile(args[0])
				if !ok {
					return false, false, cerr.New(cerr.ExitNotFound, "no profile %q", args[0])
				}
				cleanup = auth.SnapshotLoginKeys(*p, cr.Profiles[args[0]])
				c.Remove(args[0])
				delete(cr.Profiles, args[0])
				return true, true, nil
			})
			cleanup.Revoke(cmd.Context(), a)
			return err
		},
	})
	return c
}
