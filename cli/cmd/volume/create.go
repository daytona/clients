// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package volume

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"
	apiclient "go.daytona.io/api-client-go"
	apiclient_cli "go.daytona.io/cli/apiclient"
	"go.daytona.io/cli/cmd/common"
	view_common "go.daytona.io/cli/views/common"
)

var CreateCmd = &cobra.Command{
	Use:     "create [NAME]",
	Short:   "Create a volume",
	Args:    cobra.ExactArgs(1),
	Aliases: common.GetAliases("create"),
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := context.Background()

		apiClient, err := apiclient_cli.GetApiClient(nil, nil)
		if err != nil {
			return err
		}

		volume, res, err := apiClient.VolumesAPI.CreateVolume(ctx).CreateVolume(apiclient.CreateVolume{
			Name: args[0],
		}).Execute()
		if err != nil {
			return apiclient_cli.HandleErrorResponse(res, err)
		}

		view_common.RenderInfoMessageBold(fmt.Sprintf("Volume %s successfully created", volume.Name))
		return nil
	},
}

var sizeFlag int32

func init() {
	CreateCmd.Flags().Int32VarP(&sizeFlag, "size", "s", 10, "Size of the volume in GB")
}
