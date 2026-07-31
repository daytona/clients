// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package organization

import (
	"context"
	"fmt"

	apiclient "github.com/daytona/clients/api-client-go"
	apiclient_cli "github.com/daytona/clients/cli/apiclient"
	"github.com/daytona/clients/cli/cmd/common"
	"github.com/daytona/clients/cli/config"
	"github.com/daytona/clients/cli/views/organization"
	"github.com/spf13/cobra"
)

var MembersCmd = &cobra.Command{
	Use:   "members [ORGANIZATION]",
	Short: "List members of an organization",
	Long:  "List members of an organization. Defaults to the active organization if none is specified.",
	Args:  cobra.MaximumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := context.Background()

		apiClient, err := apiclient_cli.GetApiClient(nil, nil)
		if err != nil {
			return err
		}

		organizationId, err := resolveOrganizationId(ctx, apiClient, args)
		if err != nil {
			return err
		}

		memberList, res, err := apiClient.OrganizationsAPI.ListOrganizationMembers(ctx, organizationId).Execute()
		if err != nil {
			return apiclient_cli.HandleErrorResponse(res, err)
		}

		if common.FormatFlag != "" {
			formattedData := common.NewFormatter(memberList)
			formattedData.Print()
			return nil
		}

		organization.ListOrganizationMembers(memberList)
		return nil
	},
}

func resolveOrganizationId(ctx context.Context, apiClient *apiclient.APIClient, args []string) (string, error) {
	if len(args) == 0 {
		return config.GetActiveOrganizationId()
	}

	orgList, res, err := apiClient.OrganizationsAPI.ListOrganizations(ctx).Execute()
	if err != nil {
		return "", apiclient_cli.HandleErrorResponse(res, err)
	}

	for _, org := range orgList {
		if org.Id == args[0] || org.Name == args[0] {
			return org.Id, nil
		}
	}

	return "", fmt.Errorf("organization %s not found", args[0])
}

func init() {
	common.RegisterFormatFlag(MembersCmd)
}
