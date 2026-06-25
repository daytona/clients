// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package organization

import (
	"errors"

	"github.com/spf13/cobra"
	"go.daytona.io/cli/config"
	"go.daytona.io/cli/internal"
)

var OrganizationCmd = &cobra.Command{
	Use:     "organization",
	Short:   "Manage Daytona organizations",
	Long:    "Commands for managing Daytona organizations",
	Aliases: []string{"organizations", "org", "orgs"},
	GroupID: internal.USER_GROUP,
	PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
		if config.IsApiKeyAuth() {
			return errors.New("organization commands are not available when using API key authentication - run `daytona login` to reauthenticate with browser")
		}

		return nil
	},
}

func init() {
	OrganizationCmd.AddCommand(ListCmd)
	OrganizationCmd.AddCommand(CreateCmd)
	OrganizationCmd.AddCommand(UseCmd)
	OrganizationCmd.AddCommand(DeleteCmd)
}
