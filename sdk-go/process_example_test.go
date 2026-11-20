// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"
	"os"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleSandbox_Exec() {
	ctx := context.Background()
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	sb, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	res, err := sb.Exec(ctx, "python3 -c 'print(1)'", nil)
	if err != nil {
		log.Fatal(err)
	} // API, transport or cancellation failure.
	fmt.Print(res.Stdout)
	if res.ExitCode != nil && *res.ExitCode != 0 {
		fmt.Fprintf(os.Stderr, "command exited %d: %s", *res.ExitCode, res.Stderr)
	}
}

func ExampleSandbox_ExecArgs() {
	ctx := context.Background()
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	sb, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	// An argument containing shell syntax remains literal.
	result, err := sb.ExecArgs(ctx, []string{"printf", "%s", "$(not-a-command)"}, &daytona.ExecOptions{Stdout: os.Stdout})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(result.ProcessID)
}

func ExampleProcessService_Start() {
	ctx := context.Background()
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	sb, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	handle, err := sb.Process().Start(ctx, "python3 -m http.server 8080", &daytona.ProcessStartOptions{Tag: "web"})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(handle.ID()) // Store handle.ToJSON() to reconnect on another worker.
	if err = handle.Kill(ctx, nil); err != nil {
		log.Print(err)
	}
}

func ExampleProcessService_CreateSession() {
	ctx := context.Background()
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	sb, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	session, err := sb.Process().CreateSession(ctx, &daytona.CreateSessionOptions{Cwd: "/tmp"})
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if deleteErr := session.Delete(ctx); deleteErr != nil {
			log.Print(deleteErr)
		}
	}()
	if _, err = session.Run(ctx, "export ANSWER=42", nil); err != nil {
		log.Fatal(err)
	}
	result, err := session.Run(ctx, `printf '%s' "$ANSWER"`, nil)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(result.Stdout)
}
