// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleImages_BuildAndWait() {
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

	// Build once, then use the image ID for many sandboxes.
	builder := daytona.NewImageBuilder("python:3.12-slim").PipInstall("pandas")
	image, err := client.Images().BuildAndWait(ctx, builder, &daytona.BuildImageWaitOptions{
		BuildImageOptions: daytona.BuildImageOptions{Name: "python312-pandas"},
		Timeout:           10 * time.Minute,
	})
	if err != nil {
		fmt.Println(err)
		return
	}
	fmt.Println(image.ID)

	// With the sandbox lifecycle API:
	// sandbox, err := client.Create(ctx, image.ID, nil)
	// Use the sandbox, then sandbox.Delete(ctx, nil) before deleting the image.
	// Keep the same Region on build and create when selecting one explicitly.
	if err = client.Images().Delete(ctx, image.ID); err != nil {
		fmt.Println(err)
	}
}

func ExampleImages_BuildFromContext() {
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

	image, err := client.Images().BuildFromContext(ctx, "./app", &daytona.BuildFromContextOptions{
		Dockerfile: "docker/Dockerfile",
	})
	if err != nil {
		fmt.Println(err)
		return
	}
	image, err = client.Images().Get(ctx, image.ID, &daytona.GetArtifactOptions{
		Wait: true, Timeout: 10 * time.Minute,
	})
	if err != nil {
		fmt.Println(err)
		return
	}
	fmt.Println(image.Status)
}
