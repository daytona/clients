// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"
	"os"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleImages_FollowBuildLogs() {
	client, err := daytona.New()
	if err != nil {
		log.Print(err)
		return
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	for event, followErr := range client.Images().FollowBuildLogs(context.Background(), "image-id", nil) {
		if followErr != nil {
			log.Print(followErr)
			return
		}
		switch value := event.(type) {
		case daytona.BuildLogChunkEvent:
			if _, writeErr := os.Stdout.Write(value.Data); writeErr != nil {
				log.Print(writeErr)
				return
			}
			// Persist value.Cursor together with the output to resume safely.
		case daytona.BuildLogDoneEvent:
			fmt.Printf("status=%s complete=%t\n", value.Result.Status, value.Result.OutputComplete)
		}
	}
}

func ExampleSandbox_BuildLogs() {
	client, err := daytona.New()
	if err != nil {
		log.Print(err)
		return
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	ctx := context.Background()
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Print(err)
		return
	}
	snapshot, err := sandbox.BuildLogs(ctx)
	if err != nil {
		log.Print(err)
		return
	}
	fmt.Print(snapshot.Text)
}
