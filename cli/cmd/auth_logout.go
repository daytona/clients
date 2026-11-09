// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"github.com/daytona/clients/cli/internal/auth"
	"github.com/spf13/cobra"
)

func newLogoutCmd() *cobra.Command {
	return &cobra.Command{
		Use: "logout", Short: "Remove the saved session and revoke its CLI-minted project keys",
		Long: `Deletes the active profile's saved tokens and cached workload credentials from
this machine, then attempts to revoke its cached login-minted project keys against
the profile's saved API (at most 3 seconds total). Local logout completes even
when that API is unavailable. Keys whose revocation fails expire server-side.

Does not revoke manually supplied credentials, keys an earlier release exchanged
from them, other profiles' keys, keys from other APIs, or the identity
provider's session.
Keys no longer tracked after credential replacement or identity change are
not revoked.
--api-key and API URL overrides are not used for logout cleanup.`,
		GroupID: GroupAuth, Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			if err := auth.Logout(cmd.Context(), a); err != nil {
				return err
			}
			a.Info("Logged out of profile %q (local session removed).", a.Profile.Name)
			return nil
		},
	}
}
