// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package volume

import (
	"context"

	"github.com/spf13/cobra"
	"go.daytona.io/cli/apiclient"
	"go.daytona.io/cli/cmd/common"
	"go.daytona.io/cli/config"
	"go.daytona.io/cli/views/volume"
)

var ListCmd = &cobra.Command{
	Use:     "list",
	Short:   "List all volumes",
	Args:    cobra.NoArgs,
	Aliases: common.GetAliases("list"),
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := context.Background()

		apiClient, err := apiclient.GetApiClient(nil, nil)
		if err != nil {
			return err
		}

		volumes, res, err := apiClient.VolumesAPI.ListVolumes(ctx).Execute()
		if err != nil {
			return apiclient.HandleErrorResponse(res, err)
		}

		if common.FormatFlag != "" {
			formattedData := common.NewFormatter(volumes)
			formattedData.Print()
			return nil
		}

		var activeOrganizationName *string

		if !config.IsApiKeyAuth() {
			name, err := common.GetActiveOrganizationName(apiClient, ctx)
			if err != nil {
				return err
			}
			activeOrganizationName = &name
		}

		volume.ListVolumes(volumes, activeOrganizationName)
		return nil
	},
}

func init() {
	common.RegisterFormatFlag(ListCmd)
}
