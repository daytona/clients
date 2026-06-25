// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package sandbox

import (
	"context"

	"github.com/spf13/cobra"
	"go.daytona.io/cli/apiclient"
	"go.daytona.io/cli/cmd/common"
	"go.daytona.io/cli/views/sandbox"
)

var InfoCmd = &cobra.Command{
	Use:     "info [SANDBOX_ID] | [SANDBOX_NAME]",
	Short:   "Get sandbox info",
	Args:    cobra.ExactArgs(1),
	Aliases: common.GetAliases("info"),
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := context.Background()

		apiClient, err := apiclient.GetApiClient(nil, nil)
		if err != nil {
			return err
		}

		sandboxIdOrNameArg := args[0]

		sb, res, err := apiClient.SandboxAPI.GetSandbox(ctx, sandboxIdOrNameArg).Execute()
		if err != nil {
			return apiclient.HandleErrorResponse(res, err)
		}

		if common.FormatFlag != "" {
			formattedData := common.NewFormatter(sb)
			formattedData.Print()
			return nil
		}

		sandbox.RenderInfo(sb, false)

		return nil
	},
}

func init() {
	common.RegisterFormatFlag(InfoCmd)
}
