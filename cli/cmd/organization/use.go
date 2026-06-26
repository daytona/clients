// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package organization

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"
	apiclient "go.daytona.io/api-client-go"
	apiclient_cli "go.daytona.io/cli/apiclient"
	"go.daytona.io/cli/config"
	"go.daytona.io/cli/views/common"
	"go.daytona.io/cli/views/organization"
	"go.daytona.io/cli/views/util"
)

var UseCmd = &cobra.Command{
	Use:   "use [ORGANIZATION]",
	Short: "Set active organization",
	Args:  cobra.MaximumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		var chosenOrganization *apiclient.Organization
		ctx := context.Background()

		apiClient, err := apiclient_cli.GetApiClient(nil, nil)
		if err != nil {
			return err
		}

		orgList, res, err := apiClient.OrganizationsAPI.ListOrganizations(ctx).Execute()
		if err != nil {
			return apiclient_cli.HandleErrorResponse(res, err)
		}

		if len(orgList) == 0 {
			util.NotifyEmptyOrganizationList(true)
			return nil
		}

		if len(args) == 0 {
			chosenOrganization, err = organization.GetOrganizationIdFromPrompt(orgList)
			if err != nil {
				return err
			}
		} else {
			for _, org := range orgList {
				if org.Id == args[0] || org.Name == args[0] {
					chosenOrganization = &org
					break
				}
			}

			if chosenOrganization == nil {
				return fmt.Errorf("organization %s not found", args[0])
			}
		}

		c, err := config.GetConfig()
		if err != nil {
			return err
		}

		activeProfile, err := c.GetActiveProfile()
		if err != nil {
			return err
		}

		activeProfile.ActiveOrganizationId = &chosenOrganization.Id
		err = c.EditProfile(activeProfile)
		if err != nil {
			return err
		}

		common.RenderInfoMessageBold(fmt.Sprintf("Organization %s is now active", chosenOrganization.Name))
		return nil
	},
}
