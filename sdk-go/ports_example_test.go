// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"log"
	"net/http"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func ExamplePorts_Expose() {
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
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	endpoint, err := sandbox.Ports().Expose(ctx, 8080, &daytona.ExposePortOptions{Public: true})
	if err != nil {
		log.Fatal(err)
	}
	// Public endpoints require no preview token.
	_ = endpoint.URL
}

func ExamplePorts_PreviewLink() {
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
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	link, err := sandbox.Ports().PreviewLink(ctx, 8080)
	if err != nil {
		log.Fatal(err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, link.URL, nil)
	if err != nil {
		log.Fatal(err)
	}
	request.Header.Set("X-Daytona-Preview-Token", link.Token)
	request.Header.Set(daytona.PreviewWarningSkipHeader, daytona.PreviewWarningSkipHeaderValue)
	// Send request using an HTTP client with a suitable timeout.
}

func ExamplePorts_SignedPreviewLink() {
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
	sandbox, err := client.Get(ctx, "sandbox-id", nil)
	if err != nil {
		log.Fatal(err)
	}
	link, err := sandbox.Ports().SignedPreviewLink(ctx, 8080, &daytona.SignedPreviewLinkOptions{ExpiresIn: 5 * time.Minute})
	if err != nil {
		log.Fatal(err)
	}
	// Hand link.URL to a browser or another tool: no headers are required.
	_ = link.URL
	if revokeErr := sandbox.Ports().ExpireSignedPreviewLinks(ctx); revokeErr != nil {
		log.Fatal(revokeErr)
	}
}
