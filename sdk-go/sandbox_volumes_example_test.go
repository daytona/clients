// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleSandboxVolumesService_Pull() {
	ctx := context.Background()
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	// Persist this caller-owned identity and reuse it to check or retry this pull.
	result, err := sandbox.Volumes().Pull(ctx, "vol_attached", "pull_1", &daytona.PullVolumeOptions{Timeout: daytona.NoWait})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(result.RequestID, result.Status)
}
