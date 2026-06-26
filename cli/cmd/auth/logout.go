// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"github.com/spf13/cobra"
	"go.daytona.io/cli/config"
	"go.daytona.io/cli/internal"
	"go.daytona.io/cli/views/common"
)

var LogoutCmd = &cobra.Command{
	Use:     "logout",
	Short:   "Logout from Daytona",
	Args:    cobra.NoArgs,
	GroupID: internal.USER_GROUP,
	RunE: func(cmd *cobra.Command, args []string) error {
		c, err := config.GetConfig()
		if err != nil {
			return err
		}

		activeProfile, err := c.GetActiveProfile()
		if err != nil {
			return err
		}

		// For now, this just clears the local auth token/api key entries
		activeProfile.Api.Token = nil
		activeProfile.Api.Key = nil

		err = c.EditProfile(activeProfile)
		if err != nil {
			return err
		}

		common.RenderInfoMessageBold("Successfully logged out")
		return nil
	},
}
