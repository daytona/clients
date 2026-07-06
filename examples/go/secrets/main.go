// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/daytona/clients/sdk-go/pkg/daytona"
	"github.com/daytona/clients/sdk-go/pkg/types"
)

func main() {
	// Create a new Daytona client using environment variables.
	// Set DAYTONA_API_KEY before running.
	client, err := daytona.NewClient()
	if err != nil {
		log.Fatalf("Failed to create client: %v", err)
	}

	ctx := context.Background()

	// Create an organization secret. The plaintext value is stored encrypted and is
	// never returned by the API again. Use Hosts to restrict where the value may be sent.
	secretName := fmt.Sprintf("example-api-key-%d", time.Now().Unix())
	description := "Example secret created by the Daytona SDK"
	secret, err := client.Secret.Create(ctx, &types.CreateSecretParams{
		Name:        secretName,
		Value:       "sk-ant-example-secret-value",
		Description: &description,
		Hosts:       []string{"api.anthropic.com", "*.anthropic.com"},
	})
	if err != nil {
		log.Fatalf("Failed to create secret: %v", err)
	}
	log.Printf("✓ Created secret %q (ID: %s)\n", secret.Name, secret.ID)
	// The injected env var holds this opaque placeholder, never the plaintext value.
	log.Printf("  Injected placeholder: %s\n", secret.Placeholder)

	// List all secrets in the organization, following the pagination cursor
	// until there are no more pages.
	query := &types.ListSecretsQuery{}
	var secrets []*types.Secret
	for {
		page, err := client.Secret.List(ctx, query)
		if err != nil {
			log.Fatalf("Failed to list secrets: %v", err)
		}
		secrets = append(secrets, page.Items...)
		if page.NextCursor == nil {
			break
		}
		query.Cursor = page.NextCursor
	}
	log.Printf("Organization has %d secret(s)\n", len(secrets))

	// Create a sandbox that mounts the secret as the env var ANTHROPIC_API_KEY.
	// The Secrets map is {envVarName: existingSecretName}.
	params := types.SnapshotParams{
		SandboxBaseParams: types.SandboxBaseParams{
			Language: types.CodeLanguagePython,
			Secrets:  map[string]string{"ANTHROPIC_API_KEY": secretName},
		},
	}
	sandbox, err := client.Create(ctx, params)
	if err != nil {
		log.Fatalf("Failed to create sandbox: %v", err)
	}
	log.Printf("✓ Created sandbox: %s\n", sandbox.ID)

	// Inside the sandbox the env var holds the opaque placeholder, never the plaintext.
	// The real value is substituted transparently only on outbound requests to the
	// secret's allowed hosts.
	result, err := sandbox.Process.ExecuteCommand(ctx, "echo $ANTHROPIC_API_KEY")
	if err != nil {
		log.Fatalf("Failed to execute command: %v", err)
	}
	log.Printf("ANTHROPIC_API_KEY inside sandbox: %s\n", result.Result)

	// Rotate the secret value and narrow its allowed hosts. Nil fields are left unchanged.
	newValue := "sk-ant-rotated-value"
	updated, err := client.Secret.Update(ctx, secret.ID, &types.UpdateSecretParams{
		Value: &newValue,
		Hosts: []string{"api.anthropic.com"},
	})
	if err != nil {
		log.Fatalf("Failed to update secret: %v", err)
	}
	log.Printf("✓ Updated secret %q; allowed hosts: %v\n", updated.Name, updated.Hosts)

	// Clean up
	if err := sandbox.Delete(ctx); err != nil {
		log.Printf("Failed to delete sandbox: %v", err)
	}
	if err := client.Secret.Delete(ctx, secret.ID); err != nil {
		log.Printf("Failed to delete secret: %v", err)
	}
	log.Println("✓ Deleted sandbox and secret")
}
