// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"context"
	"log"
	"os"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleSecrets_Set() {
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
	secret, err := client.Secrets().Set(ctx, "SERVICE_TOKEN", os.Getenv("SERVICE_TOKEN"), &daytona.SetSecretOptions{
		AllowedDomains: []string{"api.example.com"},
		IfNotExists:    true,
	})
	if err != nil {
		log.Fatal(err)
	}
	log.Print(secret.Name, secret.AllowedDomains) // Never contains the value.
	if deleteErr := client.Secrets().Delete(ctx, secret.Name); deleteErr != nil {
		log.Print(deleteErr)
	}
}
