// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package common

import (
	"context"
	"fmt"
	"time"

	apiclient "go.daytona.io/api-client-go"
	apiclient_cli "go.daytona.io/cli/apiclient"
)

func AwaitSnapshotState(ctx context.Context, apiClient *apiclient.APIClient, name string, states ...apiclient.SnapshotState) error {
	for {
		snapshot, res, err := apiClient.SnapshotsAPI.GetSnapshot(ctx, name).Execute()
		if err != nil {
			return apiclient_cli.HandleErrorResponse(res, err)
		}

		for _, s := range states {
			if snapshot.State == s {
				return nil
			}
		}

		switch snapshot.State {
		case apiclient.SNAPSHOTSTATE_ERROR, apiclient.SNAPSHOTSTATE_BUILD_FAILED:
			if !snapshot.ErrorReason.IsSet() {
				return fmt.Errorf("snapshot processing failed")
			}
			return fmt.Errorf("snapshot processing failed: %s", *snapshot.ErrorReason.Get())
		}

		time.Sleep(time.Second)
	}
}

func AwaitSandboxState(ctx context.Context, apiClient *apiclient.APIClient, targetSandbox string, states ...apiclient.SandboxState) error {
	for {
		sandbox, res, err := apiClient.SandboxAPI.GetSandbox(ctx, targetSandbox).Execute()
		if err != nil {
			return apiclient_cli.HandleErrorResponse(res, err)
		}

		if sandbox.State != nil {
			for _, s := range states {
				if *sandbox.State == s {
					return nil
				}
			}
			if *sandbox.State == apiclient.SANDBOXSTATE_ERROR || *sandbox.State == apiclient.SANDBOXSTATE_BUILD_FAILED {
				if sandbox.ErrorReason == nil {
					return fmt.Errorf("sandbox processing failed")
				}
				return fmt.Errorf("sandbox processing failed: %s", *sandbox.ErrorReason)
			}
		}

		time.Sleep(time.Second)
	}
}
