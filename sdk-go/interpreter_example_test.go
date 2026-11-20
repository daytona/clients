// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"
	"os"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleSandbox_RunCode() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
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
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Print(err)
		return
	}
	result, err := sandbox.RunCode(ctx, "print(40 + 2)", &daytona.RunCodeOptions{
		Stdout: os.Stdout, Timeout: 30 * time.Second,
	})
	if err != nil {
		log.Print(err)
		return
	}
	if result.Error != nil {
		fmt.Println(result.Error.Name, result.Error.Value)
	}
}

func ExampleInterpreter_CreateContext() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
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
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Print(err)
		return
	}
	kernel, err := sandbox.Interpreter().CreateContext(ctx, "python", nil)
	if err != nil {
		log.Print(err)
		return
	}
	defer func() {
		if closeErr := kernel.Close(ctx); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	if _, err = kernel.Run(ctx, "x = 21", nil); err != nil {
		log.Print(err)
		return
	}
	result, err := kernel.Run(ctx, "print(x * 2)", nil)
	if err != nil {
		log.Print(err)
		return
	}
	fmt.Print(result.Logs.Stdout)
}
