// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleClient_CreateFromBuild() {
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

	builder := daytona.NewImageBuilder("python:3.12-slim").PipInstall("six==1.17.0")
	sandbox, err := client.CreateFromBuild(ctx, builder, &daytona.CreateFromBuildOptions{
		CreateOptions: daytona.CreateOptions{Wait: time.Minute},
		BuildTimeout:  4 * time.Minute,
		IdleExpiry:    time.Hour, // The anonymous image expires when idle.
	})
	if sandbox != nil {
		defer func() {
			if deleteErr := sandbox.Delete(context.WithoutCancel(ctx), nil); deleteErr != nil {
				fmt.Println(deleteErr)
			}
		}()
	}
	if err != nil {
		fmt.Println(err)
		return
	}
	result, err := sandbox.Exec(ctx, `python3 -c "import six; print(six.__version__)"`, nil)
	if err != nil {
		fmt.Println(err)
		return
	}
	fmt.Print(result.Stdout)
}
