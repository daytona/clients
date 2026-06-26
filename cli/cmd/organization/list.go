// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package organization

import (
	"context"

	"github.com/spf13/cobra"
	"go.daytona.io/cli/apiclient"
	"go.daytona.io/cli/cmd/common"
	"go.daytona.io/cli/config"
	"go.daytona.io/cli/views/organization"
)

var ListCmd = &cobra.Command{
	Use:     "list",
	Short:   "List all organizations",
	Args:    cobra.NoArgs,
	Aliases: common.GetAliases("list"),
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := context.Background()

		apiClient, err := apiclient.GetApiClient(nil, nil)
		if err != nil {
			return err
		}

		organizationList, res, err := apiClient.OrganizationsAPI.ListOrganizations(ctx).Execute()
		if err != nil {
			return apiclient.HandleErrorResponse(res, err)
		}

		if common.FormatFlag != "" {
			formattedData := common.NewFormatter(organizationList)
			formattedData.Print()
			return nil
		}

		activeOrganizationId, err := config.GetActiveOrganizationId()
		if err != nil {
			return err
		}

		organization.ListOrganizations(organizationList, &activeOrganizationId)
		return nil
	},
}

func init() {
	common.RegisterFormatFlag(ListCmd)
}
