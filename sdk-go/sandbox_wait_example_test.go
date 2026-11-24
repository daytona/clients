// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"errors"
	"fmt"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleSandbox_Wait() {
	ctx := context.Background()
	client, err := daytona.New()
	if err != nil {
		fmt.Println(err)
		return
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			fmt.Println(closeErr)
		}
	}()
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		fmt.Println(err)
		return
	}
	err = sandbox.Wait(ctx, &daytona.WaitOptions{
		Until:   []daytona.SandboxState{daytona.SandboxStateStarted},
		Timeout: 30 * time.Second,
	})
	switch {
	case errors.Is(err, daytona.ErrRequestTimeout):
		fmt.Println("Still waiting:", sandbox.State())
	case err != nil:
		fmt.Println(err)
	default:
		fmt.Println("Ready:", sandbox.ID())
	}
}
