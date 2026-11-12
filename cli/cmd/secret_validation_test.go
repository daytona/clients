// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestSecretSet_rejectsEmptyDomain_whenExplicitlyPassed(t *testing.T) {
	for _, domains := range [][]string{{""}, {" "}, {"example.com,"}, {"example.com", ""}, {",example.com"}} {
		t.Run(domains[0], func(t *testing.T) {
			// Given a scoped secret and a list containing an empty domain.
			api := newFakeAPI(t)
			api.on("GET", "/secrets/SCOPED", 200, map[string]any{
				"name": "SCOPED", "allowed_domains": []string{"api.example.com"},
				"created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
			})
			args := []string{"secret", "set", "SCOPED", "--value", "next"}
			for _, domain := range domains {
				args = append(args, "--allowed-domain", domain)
			}
			// When setting the secret.
			_, stderr, code := runMgmt(t, api, cfgDir(t), args...)
			// Then invalid input cannot be mistaken for preserving the old scope.
			require.Equal(t, cerr.ExitUsage, code, stderr)
			require.Contains(t, stderr, "invalid allowed_domains entry")
			require.Empty(t, api.requests)
		})
	}
}

func TestSecretSet_replacesDomains_whenWildcardPassed(t *testing.T) {
	// Given a secret restricted to one domain.
	api := newFakeAPI(t)
	api.on("GET", "/secrets/SCOPED", 200, map[string]any{
		"name": "SCOPED", "allowed_domains": []string{"api.example.com"},
		"created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	})
	api.on("PUT", "/secrets/SCOPED", 200, map[string]any{
		"name": "SCOPED", "allowed_domains": []string{"*"},
		"created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	})
	// When removing the domain restriction explicitly.
	_, stderr, code := runMgmt(t, api, cfgDir(t), "secret", "set", "SCOPED", "--value", "next", "--allowed-domain", "*")
	// Then the stored scope is replaced, not preserved or emptied.
	require.Equal(t, 0, code, stderr)
	require.Equal(t, []any{"*"}, findRequest(t, api, "PUT", "/secrets/SCOPED").Body["allowed_domains"])
}
