// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package sandbox

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"
	"go.daytona.io/cli/apiclient"
	view_common "go.daytona.io/cli/views/common"
)

var StartCmd = &cobra.Command{
	Use:   "start [SANDBOX_ID] | [SANDBOX_NAME]",
	Short: "Start a sandbox",
	Args:  cobra.MaximumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := context.Background()

		apiClient, err := apiclient.GetApiClient(nil, nil)
		if err != nil {
			return err
		}

		sandboxIdOrNameArg := args[0]

		_, res, err := apiClient.SandboxAPI.StartSandbox(ctx, sandboxIdOrNameArg).Execute()
		if err != nil {
			return apiclient.HandleErrorResponse(res, err)
		}

		view_common.RenderInfoMessageBold(fmt.Sprintf("Sandbox %s started", sandboxIdOrNameArg))

		return nil
	},
}

func init() {
}
