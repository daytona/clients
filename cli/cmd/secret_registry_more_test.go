// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestSecretSet_preservesExistingMetadataWhenValueOnly(t *testing.T) {
	api := newFakeAPI(t)
	api.on("GET", "/secrets/API_KEY", 200, map[string]any{
		"name":            "API_KEY",
		"allowed_domains": []string{"example.com", "*.corp.test"},
		"description":     "existing",
		"created_at":      "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	})
	api.on("PUT", "/secrets/API_KEY", 200, map[string]any{
		"name": "API_KEY", "allowed_domains": []string{"example.com", "*.corp.test"}, "description": "existing", "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	})

	_, stderr, code := runMgmt(t, api, cfgDir(t), "--project", "proj-1", "secret", "set", "API_KEY", "--value", "next")
	require.Equal(t, 0, code, stderr)
	require.Len(t, api.requests, 2)
	require.Equal(t, []any{"example.com", "*.corp.test"}, api.requests[1].Body["allowed_domains"])
	require.Equal(t, "existing", api.requests[1].Body["description"])
	require.Equal(t, "next", api.requests[1].Body["value"])
}

func TestRegistrySet_preservesExistingDescriptionWhenPasswordOnly(t *testing.T) {
	api := newFakeAPI(t)
	api.on("GET", "/registries/ghcr.io", 200, map[string]any{
		"host":        "ghcr.io",
		"kind":        "ghcr",
		"username":    "me",
		"description": "existing",
		"created_at":  "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	})
	api.on("PUT", "/registries/ghcr.io", 200, map[string]any{
		"host": "ghcr.io", "kind": "ghcr", "username": "me", "description": "existing", "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	})

	_, stderr, code := runMgmt(t, api, cfgDir(t), "--project", "proj-1", "registry", "set", "ghcr.io", "--username", "me", "--password", "pw")
	require.Equal(t, 0, code, stderr)
	put := findRequest(t, api, "PUT", "/registries/ghcr.io")
	require.Equal(t, "existing", put.Body["description"])
	require.Equal(t, "pw", put.Body["password"])
	// `kind` is carried over too: the flag default (`basic`) must not
	// silently downgrade a ghcr registry on a password rotation.
	require.Equal(t, "ghcr", put.Body["kind"])

	// An explicit --kind still wins.
	api2 := newFakeAPI(t)
	api2.on("GET", "/registries/ghcr.io", 200, map[string]any{"host": "ghcr.io", "kind": "ghcr", "username": "me", "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	api2.on("PUT", "/registries/ghcr.io", 200, map[string]any{"host": "ghcr.io", "kind": "basic", "username": "me", "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	_, stderr, code = runMgmt(t, api2, cfgDir(t), "--project", "proj-1", "registry", "set", "ghcr.io", "--username", "me", "--password", "pw", "--kind", "basic")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "basic", findRequest(t, api2, "PUT", "/registries/ghcr.io").Body["kind"])
}

func TestSecretSet_createWithoutAllowedDomainExitsBeforePut(t *testing.T) {
	api := newFakeAPI(t)
	// No GET registered — fakeAPI returns 404, so secretPutReq treats this as a create.
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--project", "proj-1", "secret", "set", "NEW_KEY", "--value", "v")
	require.Equal(t, 2, code, "expected usage error exit code 2")
	require.Contains(t, stderr, "--allowed-domain '*'")
	require.Len(t, api.requests, 1, "only the GET should have been sent; no PUT")
	require.Equal(t, "GET", api.requests[0].Method)
}

func TestSecretSet_updateEmptyScopedWithoutFlagErrors(t *testing.T) {
	api := newFakeAPI(t)
	api.on("GET", "/secrets/EXISTING", 200, map[string]any{
		"name":            "EXISTING",
		"allowed_domains": []any{},
		"created_at":      "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	})

	_, stderr, code := runMgmt(t, api, cfgDir(t), "--project", "proj-1", "secret", "set", "EXISTING", "--value", "v")
	require.Equal(t, 2, code, "expected usage error exit code 2")
	require.Contains(t, stderr, "--allowed-domain '*'")
	require.Len(t, api.requests, 1, "only the GET should have been sent; no PUT")
}

func TestSecretSet_emptyAllowedDomainFlagCountsAsNoScope(t *testing.T) {
	api := newFakeAPI(t)
	// No GET registered — fakeAPI returns 404 (create case).
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--project", "proj-1", "secret", "set", "NEW_KEY", "--value", "v", "--allowed-domain", "")
	require.Equal(t, 2, code, "expected usage error exit code 2")
	require.Contains(t, stderr, "--allowed-domain is required when creating a secret")
	require.Len(t, api.requests, 1, "only the GET should have been sent; no PUT")

	scoped := newFakeAPI(t)
	scoped.on("GET", "/secrets/SCOPED", 200, map[string]any{
		"name":            "SCOPED",
		"allowed_domains": []any{"api.example.com"},
		"created_at":      "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
	})
	scoped.on("PUT", "/secrets/SCOPED", 200, map[string]any{"name": "SCOPED", "allowed_domains": []any{"api.example.com"}, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	_, stderr, code = runMgmt(t, scoped, cfgDir(t), "--project", "proj-1", "secret", "set", "SCOPED", "--value", "v", "--allowed-domain", "")
	require.Equal(t, 0, code, stderr)
	put := findRequest(t, scoped, "PUT", "/secrets/SCOPED")
	require.Equal(t, []any{"api.example.com"}, put.Body["allowed_domains"], "an empty flag must not clear the stored scope")
}

func TestSecretSet_createWithAllowedDomainSucceeds(t *testing.T) {
	api := newFakeAPI(t)
	// No GET registered — fakeAPI returns 404 (create case).
	api.on("PUT", "/secrets/NEW_KEY", 201, map[string]any{"name": "NEW_KEY", "allowed_domains": []any{"*"}, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})

	_, stderr, code := runMgmt(t, api, cfgDir(t), "--project", "proj-1", "secret", "set", "NEW_KEY", "--value", "v", "--allowed-domain", "*")
	require.Equal(t, 0, code, stderr)
	put := findRequest(t, api, "PUT", "/secrets/NEW_KEY")
	require.Equal(t, []any{"*"}, put.Body["allowed_domains"])
}

// kind ecr_role takes the role ARN as --username and needs no password: the
// PUT omits `password`. Other kinds still demand one.
func TestRegistrySet_ecrRoleNeedsNoPassword(t *testing.T) {
	const host = "123456789012.dkr.ecr.us-east-1.amazonaws.com"
	const role = "arn:aws:iam::123456789012:role/pull"
	api := newFakeAPI(t)
	api.on("GET", "/registries/"+host, 404, map[string]any{"code": "NOT_FOUND", "message": "not found", "source": "DAYTONA_API"})
	api.on("PUT", "/registries/"+host, 201, map[string]any{"host": host, "kind": "ecr_role", "username": role, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--project", "proj-1", "registry", "set", host, "--kind", "ecr_role", "--username", role)
	require.Equal(t, 0, code, stderr)
	put := findRequest(t, api, "PUT", "/registries/"+host)
	require.Equal(t, "ecr_role", put.Body["kind"])
	require.Equal(t, role, put.Body["username"])
	_, hasPassword := put.Body["password"]
	require.False(t, hasPassword, "no password is sent for ecr_role")

	_, _, code = runMgmt(t, newFakeAPI(t), cfgDir(t), "--project", "proj-1", "registry", "set", "ghcr.io", "--username", "me")
	require.NotEqual(t, 0, code, "basic still needs a password")

	// Rotating an existing ecr_role registry without restating --kind.
	api2 := newFakeAPI(t)
	api2.on("GET", "/registries/"+host, 200, map[string]any{"host": host, "kind": "ecr_role", "username": role, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	api2.on("PUT", "/registries/"+host, 200, map[string]any{"host": host, "kind": "ecr_role", "username": role, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	_, stderr, code = runMgmt(t, api2, cfgDir(t), "--project", "proj-1", "registry", "set", host, "--username", role)
	require.Equal(t, 0, code, stderr)
	put2 := findRequest(t, api2, "PUT", "/registries/"+host)
	require.Equal(t, "ecr_role", put2.Body["kind"])
	_, hasPassword = put2.Body["password"]
	require.False(t, hasPassword)
}

func TestSecretCommands_usePlatformTokenOnShortRoutes(t *testing.T) {
	api := newFakeAPI(t)
	issuer := installIssuer(t, api, 300)
	secret := map[string]any{"name": "API_KEY", "allowed_domains": []string{"example.com"}, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"}
	api.on(http.MethodGet, "/secrets", http.StatusOK, map[string]any{"secrets": []any{secret}, "next_cursor": "next-secret"})
	api.on(http.MethodGet, "/secrets/API_KEY", http.StatusOK, secret)
	api.on(http.MethodPut, "/secrets/API_KEY", http.StatusCreated, secret)
	api.on(http.MethodDelete, "/secrets/API_KEY", http.StatusNoContent, "")
	api.on(http.MethodDelete, "/secrets/missing", http.StatusNotFound, map[string]any{"code": "NOT_FOUND", "message": "secret not found", "source": "DAYTONA_API"})
	api.on(http.MethodGet, "/registries", http.StatusOK, map[string]any{"registries": []any{}, "next_cursor": nil})
	dir := cfgDir(t)
	oidcProfile(t, dir, api.srv.URL, time.Hour)

	stdout, stderr, code := run(t, dir, nil, "-o", "json", "secret", "list", "--limit", "1", "--cursor", "cursor-secret")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "next-secret", mustJSON(t, stdout)["next_cursor"])
	_, stderr, code = run(t, dir, nil, "-o", "json", "secret", "get", "API_KEY")
	require.Equal(t, 0, code, stderr)
	_, stderr, code = run(t, dir, nil, "-o", "json", "secret", "set", "API_KEY", "--value", "next")
	require.Equal(t, 0, code, stderr)
	_, stderr, code = run(t, dir, nil, "-o", "json", "secret", "delete", "API_KEY", "--yes")
	require.Equal(t, 0, code, stderr)
	_, stderr, code = run(t, dir, nil, "-o", "json", "secret", "delete", "missing", "--yes")
	require.Equal(t, 5, code)
	require.Contains(t, stderr, "NOT_FOUND")
	_, stderr, code = run(t, dir, nil, "-o", "json", "registry", "list")
	require.Equal(t, 0, code, stderr)

	require.Len(t, issuer.exchanges, 1)
	require.Equal(t, "cursor=cursor-secret&limit=1", findRequest(t, api, http.MethodGet, "/secrets").Query)
	for _, request := range api.requests {
		if request.Path == "/secrets" || strings.HasPrefix(request.Path, "/secrets/") || request.Path == "/registries" {
			require.True(t, strings.HasPrefix(request.Auth, "Bearer pt_"), "%s used %q", request.Path, request.Auth)
		}
		require.NotContains(t, request.Path, "/projects/")
	}
}

func TestRegistryCommands_useExplicitProjectKeyOnShortRoutes(t *testing.T) {
	api := newFakeAPI(t)
	registry := map[string]any{"host": "ghcr.io", "kind": "ghcr", "username": "me", "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"}
	api.on(http.MethodGet, "/registries", http.StatusOK, map[string]any{"registries": []any{registry}, "next_cursor": "next-registry"})
	api.on(http.MethodGet, "/registries/ghcr.io", http.StatusOK, registry)
	api.on(http.MethodPut, "/registries/ghcr.io", http.StatusOK, registry)
	api.on(http.MethodDelete, "/registries/ghcr.io", http.StatusNoContent, "")
	api.on(http.MethodDelete, "/registries/missing", http.StatusNotFound, map[string]any{"code": "NOT_FOUND", "message": "registry not found", "source": "DAYTONA_API"})
	api.on(http.MethodGet, "/registries/123456789012.dkr.ecr.us-east-1.amazonaws.com", http.StatusNotFound, map[string]any{"code": "NOT_FOUND", "message": "registry not found", "source": "DAYTONA_API"})
	api.on(http.MethodPut, "/registries/123456789012.dkr.ecr.us-east-1.amazonaws.com", http.StatusBadRequest, map[string]any{"code": "ECR_ACCOUNT_MISMATCH", "message": "credentials belong to another AWS account", "source": "DAYTONA_API"})
	api.on(http.MethodGet, "/secrets", http.StatusOK, map[string]any{"secrets": []any{}, "next_cursor": nil})
	dir := cfgDir(t)
	base := []string{"--api-url", api.srv.URL, "--api-key", "dtn_explicit", "--project", testProjP1, "-o", "json"}
	runCommand := func(args ...string) (string, string, int) {
		return run(t, dir, nil, append(base, args...)...)
	}

	stdout, stderr, code := runCommand("registry", "list", "--limit", "2", "--cursor", "cursor-registry")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "next-registry", mustJSON(t, stdout)["next_cursor"])
	_, stderr, code = runCommand("registry", "get", "ghcr.io")
	require.Equal(t, 0, code, stderr)
	_, stderr, code = runCommand("registry", "set", "ghcr.io", "--username", "me", "--password", "pw")
	require.Equal(t, 0, code, stderr)
	_, stderr, code = runCommand("registry", "delete", "ghcr.io", "--yes")
	require.Equal(t, 0, code, stderr)
	_, stderr, code = runCommand("registry", "delete", "missing", "--yes")
	require.Equal(t, 5, code)
	require.Contains(t, stderr, "NOT_FOUND")
	_, stderr, code = runCommand("registry", "set", "123456789012.dkr.ecr.us-east-1.amazonaws.com", "--kind", "ecr", "--username", "AWS", "--password", "pw")
	require.Equal(t, 2, code)
	require.Contains(t, stderr, "ECR_ACCOUNT_MISMATCH")
	_, stderr, code = runCommand("secret", "list")
	require.Equal(t, 0, code, stderr)

	require.Equal(t, "cursor=cursor-registry&limit=2", findRequest(t, api, http.MethodGet, "/registries").Query)
	for _, request := range api.requests {
		if request.Path == "/registries" || strings.HasPrefix(request.Path, "/registries/") || request.Path == "/secrets" {
			require.Equal(t, "Bearer dtn_explicit", request.Auth, request.Path)
		}
		require.NotContains(t, request.Path, "/projects/")
	}
}

func TestSecretInputValue_stripsExactlyOneTrailingNewlineFromFile(t *testing.T) {
	t.Run("lf", func(t *testing.T) {
		file := filepath.Join(t.TempDir(), "secret.txt")
		require.NoError(t, os.WriteFile(file, []byte("v\n\n"), 0o600))
		value, err := secretInputValue("", file, true, false)
		require.NoError(t, err)
		require.Equal(t, "v\n", value)
	})
	t.Run("single newline becomes empty", func(t *testing.T) {
		file := filepath.Join(t.TempDir(), "secret.txt")
		require.NoError(t, os.WriteFile(file, []byte("\n"), 0o600))
		value, err := secretInputValue("", file, true, false)
		require.NoError(t, err)
		require.Equal(t, "", value)
	})
	t.Run("crlf", func(t *testing.T) {
		file := filepath.Join(t.TempDir(), "secret.txt")
		require.NoError(t, os.WriteFile(file, []byte("v\r\n"), 0o600))
		value, err := secretInputValue("", file, true, false)
		require.NoError(t, err)
		require.Equal(t, "v", value)
	})
}

func TestSecretInputValue_stripsExactlyOneTrailingNewlineFromStdin(t *testing.T) {
	stdinFile := filepath.Join(t.TempDir(), "stdin.txt")
	require.NoError(t, os.WriteFile(stdinFile, []byte("v\n\n"), 0o600))
	opened, err := os.Open(stdinFile)
	require.NoError(t, err)
	defer opened.Close()
	oldStdin := os.Stdin
	os.Stdin = opened
	defer func() { os.Stdin = oldStdin }()

	value, err := secretInputValue("", "", false, true)
	require.NoError(t, err)
	require.Equal(t, "v\n", value)
}
