// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package organization

import (
	"context"
	"fmt"

	apiclient "github.com/daytona/clients/api-client-go"
	apiclient_cli "github.com/daytona/clients/cli/apiclient"
	"github.com/daytona/clients/cli/config"
	"github.com/daytona/clients/cli/views/common"
	"github.com/daytona/clients/cli/views/organization"
	"github.com/daytona/clients/cli/views/util"
	"github.com/spf13/cobra"
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
