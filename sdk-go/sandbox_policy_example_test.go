// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"log"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleSandbox_UpdateLifecyclePolicy() {
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	ctx := context.Background()
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	_, err = sandbox.UpdateLifecyclePolicy(ctx, &daytona.LifecyclePolicyOptions{
		Timeout: time.Hour, IdleExpiry: 10 * time.Minute, OnIdleTimeout: daytona.LifecycleActionDelete,
	})
	if err != nil {
		log.Fatal(err)
	}
}

func ExampleSandbox_UpdateNetworkPolicy() {
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	ctx := context.Background()
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	current, err := sandbox.GetNetworkPolicy(ctx)
	if err != nil {
		log.Fatal(err)
	}
	domains := []string{"api.example.com"}
	_, err = sandbox.UpdateNetworkPolicy(ctx, daytona.NetworkPolicySpec{
		AllowDomains: &domains,
	}, &daytona.IfMatchOptions{IfMatch: current.ETag})
	if err != nil {
		log.Fatal(err)
	}
}
