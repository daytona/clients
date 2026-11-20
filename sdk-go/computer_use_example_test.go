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

func ExampleSandbox_ComputerUse() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
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
	desktop := sandbox.ComputerUse()
	if _, startErr := desktop.Start(ctx, nil); startErr != nil {
		log.Fatal(startErr)
	}
	if _, clickErr := desktop.Mouse().Click(ctx, daytona.ComputerUsePoint{X: 100, Y: 200}, nil); clickErr != nil {
		log.Fatal(clickErr)
	}
	if typeErr := desktop.Keyboard().Type(ctx, "Hello desktop", nil); typeErr != nil {
		log.Fatal(typeErr)
	}
	image, err := desktop.Screen().Screenshot(ctx, nil)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("Captured %d bytes\n", len(image.Data))
}

func ExampleComputerUseRecordingService_Download() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
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
	recordings := sandbox.ComputerUse().Recording()
	recording, err := recordings.Start(ctx, nil)
	if err != nil {
		log.Fatal(err)
	}
	if typeErr := sandbox.ComputerUse().Keyboard().Hotkey(ctx, "ctrl+l"); typeErr != nil {
		log.Fatal(typeErr)
	}
	if _, stopErr := recordings.Stop(ctx, recording.ID); stopErr != nil {
		log.Fatal(stopErr)
	}
	data, err := recordings.Download(ctx, recording.ID, &daytona.ComputerUseRecordingDownloadOptions{
		MaxResponseBytes: 16 << 20,
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("Recording: %d bytes\n", len(data))
}
