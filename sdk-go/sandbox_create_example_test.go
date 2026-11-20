// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"log"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleClient_Create() {
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
	sandbox, err := client.Create(ctx, "", nil)
	if err != nil {
		log.Print(err)
		return
	}
	defer func() {
		if deleteErr := sandbox.Delete(context.WithoutCancel(ctx), nil); deleteErr != nil {
			log.Print(deleteErr)
		}
	}()
	// An accepted create can return before the sandbox is running.
	log.Printf("created %s in state %s", sandbox.ID(), sandbox.State())
}

func ExampleClient_Create_resources() {
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
	sandbox, err := client.Create(ctx, "", &daytona.CreateOptions{
		Resources: &daytona.Resources{CPU: 2, MemoryGiB: 4, DiskGiB: 8},
		LifecyclePolicy: &daytona.LifecyclePolicyOptions{
			Timeout: time.Hour, OnTimeout: daytona.LifecycleActionDelete,
			IdleExpiry: 5 * time.Minute, OnIdleTimeout: daytona.LifecycleActionDelete,
		},
	})
	if err != nil {
		log.Print(err)
		return
	}
	defer func() {
		if deleteErr := sandbox.Delete(context.WithoutCancel(ctx), nil); deleteErr != nil {
			log.Print(deleteErr)
		}
	}()
	log.Printf("sandbox %s: %s", sandbox.ID(), sandbox.State())
}
