// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"

	daytona "go.daytona.com/sdk/v2"
)

func ExamplePTY_Create() {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel() // Cancelling ctx closes the terminal connection.
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
	connection, err := sandbox.PTY().Create(ctx, "", &daytona.PTYOptions{
		Terminal: &daytona.TerminalSize{Cols: 100, Rows: 30},
	})
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := connection.Close(); closeErr != nil {
			log.Print(closeErr)
		}
		if deleteErr := sandbox.PTY().Delete(ctx, connection.Handle().ID()); deleteErr != nil {
			log.Print(deleteErr)
		}
	}()
	if _, err = connection.Write([]byte("printf 'hello\\n'\n")); err != nil {
		log.Fatal(err)
	}
	buffer := make([]byte, 4096)
	n, err := connection.Read(buffer)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Print(string(buffer[:n]))
}

func ExampleProcessHandle_Attach() {
	ctx, cancel := context.WithCancel(context.Background())
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
	handle, err := sandbox.Process().Connect("terminal-process-id")
	if err != nil {
		log.Fatal(err)
	}
	connection, err := handle.Attach(ctx)
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := connection.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	if err = connection.Resize(ctx, daytona.TerminalSize{Cols: 120, Rows: 40}); err != nil {
		log.Fatal(err)
	}
	fmt.Println(connection.Handle().ID())
}
