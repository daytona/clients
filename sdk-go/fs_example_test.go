// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleSandbox_FS() {
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	sb, err := client.Get(ctx, "your-sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	if writeErr := sb.FS().WriteFile(ctx, "/tmp/hello.txt", []byte("hello"), nil); writeErr != nil {
		log.Fatal(writeErr)
	}
	data, err := sb.FS().ReadFile(ctx, "/tmp/hello.txt", nil)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(string(data))
}

func ExampleFileSystem_Upload() {
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	sb, err := client.Get(ctx, "your-sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	if uploadErr := sb.FS().Upload(ctx, "/tmp/data.csv", strings.NewReader("a,b\n1,2\n"), nil); uploadErr != nil {
		log.Fatal(uploadErr)
	}
	if downloadErr := sb.FS().Download(ctx, "/tmp/data.csv", os.Stdout, nil); downloadErr != nil {
		log.Fatal(downloadErr)
	}
}

func ExampleFileSystem_Watch() {
	client, err := daytona.New()
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if closeErr := client.Close(); closeErr != nil {
			log.Print(closeErr)
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	sb, err := client.Get(ctx, "your-sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	watch, err := sb.FS().Watch(ctx, "/tmp", &daytona.WatchOptions{Recursive: true})
	if err != nil {
		log.Fatal(err)
	}
	defer watch.Stop()
	for event, err := range watch.Events(ctx) {
		if err != nil {
			log.Print(err)
			break
		}
		if event.Overflow {
			fmt.Println("rescan directory")
			continue
		}
		fmt.Println(event.Type, event.Name)
		break
	}
	if waitErr := watch.Wait(ctx); waitErr != nil {
		log.Print(waitErr)
	}
}
