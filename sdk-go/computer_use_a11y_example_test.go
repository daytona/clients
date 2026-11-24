// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleComputerUseA11y_Tree() {
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
	ctx := context.Background()
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		fmt.Println(err)
		return
	}
	tree, err := sandbox.ComputerUse().A11y().Tree(ctx, daytona.ComputerUseA11yScopeFocused, nil)
	if err != nil {
		fmt.Println(err)
		return
	}
	fmt.Println(tree.Root.Name, tree.Truncated)
}
