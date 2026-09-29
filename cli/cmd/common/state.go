// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"context"
	"fmt"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	apiclient_cli "github.com/daytona/clients/cli/apiclient"
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
			if *sandbox.State == apiclient.SANDBOXSTATE_DESTROYED && !containsSandboxState(states, apiclient.SANDBOXSTATE_DESTROYED) {
				if sandbox.QueueTimedOutAt != nil {
					if queueTimeout, ok := sandbox.GetQueueTimeoutOk(); ok && queueTimeout != nil {
						return fmt.Errorf("sandbox %s was destroyed after waiting %d minutes for a runner (queue timed out at %s)", sandbox.Id, *queueTimeout, *sandbox.QueueTimedOutAt)
					}
					return fmt.Errorf("sandbox %s was destroyed after waiting for a runner (queue timed out at %s)", sandbox.Id, *sandbox.QueueTimedOutAt)
				}
				if sandbox.SpotEvictedAt != nil {
					return fmt.Errorf("sandbox %s was evicted by spot preemption at %s", sandbox.Id, *sandbox.SpotEvictedAt)
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

func containsSandboxState(states []apiclient.SandboxState, want apiclient.SandboxState) bool {
	for _, state := range states {
		if state == want {
			return true
		}
	}
	return false
}
