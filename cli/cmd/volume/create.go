// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package volume

import (
	"context"
	"fmt"

	apiclient "github.com/daytona/clients/api-client-go"
	apiclient_cli "github.com/daytona/clients/cli/apiclient"
	"github.com/daytona/clients/cli/cmd/common"
	view_common "github.com/daytona/clients/cli/views/common"
	"github.com/spf13/cobra"
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

		createVolume := apiclient.CreateVolume{
			Name: args[0],
		}

		if typeFlag != "" {
			volumeType, err := apiclient.NewVolumeTypeFromValue(typeFlag)
			if err != nil {
				return fmt.Errorf("invalid volume type %q: must be one of legacy, hotmount, blockmount", typeFlag)
			}
			createVolume.Type = volumeType
		}

		if cmd.Flags().Changed("region") {
			createVolume.Region = &regionFlag
		}

		volume, res, err := apiClient.VolumesAPI.CreateVolume(ctx).CreateVolume(createVolume).Execute()
		if err != nil {
			return apiclient_cli.HandleErrorResponse(res, err)
		}

		view_common.RenderInfoMessageBold(fmt.Sprintf("Volume %s successfully created", volume.Name))
		return nil
	},
}

var (
	typeFlag   string
	regionFlag string
)

func init() {
	CreateCmd.Flags().StringVarP(&typeFlag, "type", "t", "", "Volume type (legacy, hotmount, blockmount)")
	CreateCmd.Flags().StringVarP(&regionFlag, "region", "r", "", "Region to create the volume in (blockmount: selects the region-local store its data lives in, defaults to the org's default region; sandboxes in any region can attach it. Selects the deployment region for hotmount)")
}
