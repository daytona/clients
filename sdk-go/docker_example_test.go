// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleDocker_WaitUntilReady() {
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
	status, err := sandbox.Docker().WaitUntilReady(ctx, &daytona.DockerWaitOptions{Timeout: time.Minute})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(status.State)
}
