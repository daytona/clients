// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"encoding/json"
	"fmt"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleSandbox_ToJSON() {
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
	sandbox, err := client.Get(ctx, "sbx", nil)
	if err != nil {
		fmt.Println(err)
		return
	}
	snapshot := sandbox.ToJSON()
	encoded, err := json.Marshal(snapshot)
	if err != nil {
		fmt.Println(err)
		return
	}
	fmt.Println(string(encoded))
	// Resume later with client.Get(ctx, snapshot.SandboxID, nil).
}
