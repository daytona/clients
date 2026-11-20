// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"log"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleWithSandboxEvents() {
	client, err := daytona.New(daytona.WithSandboxEvents(daytona.SandboxEventsConfig{
		ReconcileInterval: time.Minute,
		IdleClose:         30 * time.Second,
	}))
	if err != nil {
		log.Print(err)
		return
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	// Lifecycle waits share state subscriptions automatically. Unavailable
	// events fall back to API reads; Close releases every regional socket.
}
