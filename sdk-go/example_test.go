// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"errors"
	"fmt"
	"log"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleNew() {
	client, err := daytona.New(daytona.WithRequestTimeout(2 * time.Minute))
	if err != nil {
		log.Print(err)
		return
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
}

func ExampleClient_Get() {
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
	sandbox, err := client.Get(context.Background(), "sandbox-id", nil)
	if err != nil {
		log.Print(err)
		return
	}
	fmt.Println(sandbox.ID(), sandbox.State())
}

func ExampleError() {
	err := &daytona.Error{StatusCode: 404, Code: daytona.CodeFileNotFound, Source: daytona.SourceDaemon}
	fmt.Println(errors.Is(err, daytona.ErrFileNotFound))
	fmt.Println(errors.Is(err, daytona.ErrNotFound))
	var detail *daytona.Error
	if errors.As(err, &detail) {
		fmt.Println(detail.Code)
	}
	// Output:
	// true
	// true
	// FILE_NOT_FOUND
}
