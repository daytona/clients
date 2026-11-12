// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"

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
		// Listing, switching and deleting profiles must work while
		// DAYTONA_PROFILE names one that does not exist.
		Annotations: map[string]string{annotationProfileOptional: "true"},
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
				Credential string `json:"credential,omitempty"`
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
		Long:  "Make a saved profile active. Names use 1–64 ASCII letters, digits, '.', '_' or '-', excluding '.' and '..'.",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := config.ValidateProfileName(args[0]); err != nil {
				return err
			}
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
when that API is unavailable; keys whose revocation fails expire server-side.
Deleting the active profile selects default when present, otherwise leaves none
active, and announces the resulting selection on stderr.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			if err := a.Confirm("delete", "profile "+args[0]); err != nil {
				return err
			}
			if err := auth.DeleteProfile(cmd.Context(), a, args[0], func() error {
				return cerr.New(cerr.ExitNotFound, "no profile %q", args[0])
			}); err != nil {
				return err
			}
			if a.Config.ActiveProfile == args[0] {
				after, err := a.Store.LoadConfig()
				if err != nil {
					return err
				}
				if after.ActiveProfile != "" {
					_, err := fmt.Fprintf(a.Stderr, "Deleted the active profile; now using profile %q.\n", after.ActiveProfile)
					return err
				}
				_, err = fmt.Fprintln(a.Stderr, "Deleted the active profile; none is active. Use `daytona profile use NAME` or `daytona login`.")
				return err
			}
			return nil
		},
	})
	return c
}
