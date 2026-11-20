// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"log"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleSandbox_Fork() {
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
	source, err := client.Get(ctx, "source-sandbox-id", nil)
	if err != nil {
		log.Print(err)
		return
	}
	fork, err := source.Fork(ctx, nil)
	if err != nil {
		log.Print(err)
		return
	}
	defer func() {
		if deleteErr := fork.Delete(context.WithoutCancel(ctx), nil); deleteErr != nil {
			log.Print(deleteErr)
		}
	}()
	// Running sources produce started forks; paused memory forks remain paused.
	log.Printf("fork %s: %s", fork.ID(), fork.State())
}
