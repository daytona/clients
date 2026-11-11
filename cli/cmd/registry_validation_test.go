// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestRegistrySet_preservesUsername_whenUpdatingWithoutUsername(t *testing.T) {
	// Given an existing registry with a non-default kind and username.
	api := newFakeAPI(t)
	registry := map[string]any{
		"host": "registry.example.com", "kind": "ghcr", "username": "stored-user", "description": "stored description",
		"created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	}
	api.on("GET", "/registries/registry.example.com", 200, registry)
	api.on("PUT", "/registries/registry.example.com", 200, registry)
	// When rotating the password without restating metadata.
	_, stderr, code := runMgmt(t, api, cfgDir(t), "registry", "set", "registry.example.com", "--password", "replacement")
	// Then the update carries the stored fields, never empty/default replacements.
	require.Equal(t, 0, code, stderr)
	put := findRequest(t, api, "PUT", "/registries/registry.example.com")
	require.Equal(t, "stored-user", put.Body["username"])
	require.Equal(t, "ghcr", put.Body["kind"])
	require.Equal(t, "stored description", put.Body["description"])
}

func TestRegistrySet_requiresUsername_whenCreatingRegistry(t *testing.T) {
	// Given no stored registry.
	api := newFakeAPI(t)
	// When attempting a create without its username.
	_, stderr, code := runMgmt(t, api, cfgDir(t), "registry", "set", "registry.example.com", "--password", "replacement")
	// Then there is no PUT and the missing input is usage.
	require.Equal(t, cerr.ExitUsage, code, stderr)
	require.Contains(t, stderr, "--username")
	require.Len(t, api.requests, 1)
}

func TestRegistrySet_requiresPassword_whenUpdatingBasicRegistry(t *testing.T) {
	// Given an existing basic registry; the API cannot preserve its write-only password.
	api := newFakeAPI(t)
	api.on("GET", "/registries/registry.example.com", 200, map[string]any{
		"host": "registry.example.com", "kind": "basic", "username": "stored-user",
		"created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	})
	// When changing metadata without resupplying the credential.
	_, stderr, code := runMgmt(t, api, cfgDir(t), "registry", "set", "registry.example.com", "--description", "next")
	// Then the CLI does not send an update the API would refuse.
	require.Equal(t, cerr.ExitUsage, code, stderr)
	require.Len(t, api.requests, 1)
}
