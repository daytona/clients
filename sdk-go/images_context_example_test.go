// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"bytes"
	"context"
	"fmt"
	"os"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleImages_BuildFromContextReader() {
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

	archive, err := os.ReadFile("context.tar")
	if err != nil {
		fmt.Println(err)
		return
	}
	// The same call accepts archive bytes assembled in memory by your program.
	image, err := client.Images().BuildFromContextReader(
		context.Background(), bytes.NewReader(archive), int64(len(archive)), nil,
	)
	if err != nil {
		fmt.Println(err)
		return
	}
	fmt.Println(image.ID)
}
