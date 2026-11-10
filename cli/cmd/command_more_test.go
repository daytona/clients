// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/ui"
	"github.com/stretchr/testify/require"
)

func TestKeyMint_allowsOmittingNameWhenContractMakesItOptional(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodPost, "/projects/proj-1/api-keys", http.StatusCreated, map[string]any{"id": "22222222-2222-2222-2222-222222222222", "name": "server-default", "project_id": testProjP1, "created_at": "2026-09-01T00:00:00Z", "key_prefix": "dtn_abc", "key_suffix": "abc", "token": "dtn_token", "project_permissions": []string{"write:sandboxes"}, "sync_to_v0": true})
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--org", testOrgP, "--project", "proj-1", "key", "mint", "--permission", "write:sandboxes")
	require.Equal(t, 0, code, stderr)
	mint := findRequest(t, api, http.MethodPost, "/projects/proj-1/api-keys")
	_, hasName := mint.Body["name"]
	require.False(t, hasName)
}

func TestKeyMint_withoutServiceAccountDoesNotRequireOrg(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodPost, "/projects/proj-1/api-keys", http.StatusCreated, map[string]any{"id": "22222222-2222-2222-2222-222222222222", "name": "ci", "project_id": testProjP1, "created_at": "2026-09-01T00:00:00Z", "key_prefix": "dtn_abc", "key_suffix": "abc", "token": "dtn_token", "project_permissions": []string{"write:sandboxes"}, "sync_to_v0": true})
	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--project", "proj-1", "key", "mint", "--name", "ci", "--permission", "write:sandboxes")
	require.Equal(t, 0, code, stderr)
	require.Len(t, api.requests, 1)
}

func TestKeyMint_printsTokenToStdoutInTableMode(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodPost, "/projects/proj-1/api-keys", http.StatusCreated, map[string]any{"id": "22222222-2222-2222-2222-222222222222", "name": "ci", "project_id": testProjP1, "created_at": "2026-09-01T00:00:00Z", "key_prefix": "dtn_abc", "key_suffix": "abc", "token": "dtn_token", "project_permissions": []string{"write:sandboxes"}, "sync_to_v0": true})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "--quiet", "-o", "table", "key", "mint", "--name", "ci", "--permission", "write:sandboxes")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "Token (shown once): dtn_token")
}

func TestOrgKeyMint_printsTokenToStdoutInTableMode(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodPost, "/orgs/0000000a-0000-4000-8000-00000000a001/api-keys", http.StatusCreated, map[string]any{"id": "33333333-3333-3333-3333-333333333333", "name": "audit", "org_id": testOrgP, "created_at": "2026-09-01T00:00:00Z", "key_prefix": "dtno_abc", "token": "dtno_token", "org_permissions": []string{"read:audit"}})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "--quiet", "-o", "table", "org-key", "mint", "--name", "audit", "--permission", "read:audit")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "Token (shown once): dtno_token")
}

func TestOrgKeyMint_requiresNameWhenFlagUnset(t *testing.T) {
	api := newFakeAPI(t)
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--org", testOrgP, "org-key", "mint", "--permission", "read:audit")
	require.Equal(t, cerr.ExitUsage, code)
	require.Contains(t, stderr, "--name is required")
	require.Empty(t, api.requests)
}

func TestRegistryDelete_escapesHostPathSegment(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodDelete, "/registries/a?b#c", http.StatusNoContent, "")
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--project", "proj-1", "registry", "delete", "a?b#c", "--yes")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "/registries/a?b#c", api.requests[0].Path)
	require.Empty(t, api.requests[0].Query)
}

func TestSecretDelete_escapesNamePathSegment(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodDelete, "/secrets/a?b#c", http.StatusNoContent, "")
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--project", "proj-1", "secret", "delete", "a?b#c", "--yes")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "/secrets/a?b#c", api.requests[0].Path)
	require.Empty(t, api.requests[0].Query)
}

func TestProjectServiceAccountAdd_listsOnlyLiveAccounts(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/service-accounts", http.StatusOK, map[string]any{"service_accounts": []map[string]any{serviceAccountFixture("11111111-1111-1111-1111-111111111111", testOrgP, "bot")}})
	api.on(http.MethodPost, "/projects/proj-1/service-accounts", http.StatusCreated, map[string]any{"added_service_account_ids": []string{"11111111-1111-1111-1111-111111111111"}})
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "project", "service-account", "add", "bot")
	require.Equal(t, 0, code, stderr)
	require.Empty(t, api.requests[0].Query)
}

func TestProjectServiceAccountAdd_reusedNameResolvesToTheLiveAccount(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/"+testOrgP+"/service-accounts", http.StatusOK, map[string]any{"service_accounts": []map[string]any{
		serviceAccountFixture("11111111-1111-1111-1111-111111111111", testOrgP, "bot"),
		archivedServiceAccountFixture("22222222-2222-2222-2222-222222222222", testOrgP, "bot"),
	}})
	api.on(http.MethodPost, "/projects/proj-1/service-accounts", http.StatusCreated, map[string]any{"added_service_account_ids": []string{"11111111-1111-1111-1111-111111111111"}})
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--org", testOrgP, "--project", "proj-1", "project", "service-account", "add", "bot")
	require.Equal(t, 0, code, stderr)
	post := findRequest(t, api, http.MethodPost, "/projects/proj-1/service-accounts")
	require.Equal(t, "11111111-1111-1111-1111-111111111111", post.Body["service_account_id"])
}

func TestProjectServiceAccountRemove_isAmbiguousAcrossLiveAndArchivedReuse(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/"+testOrgP+"/service-accounts", http.StatusOK, map[string]any{"service_accounts": []map[string]any{
		serviceAccountFixture("11111111-1111-1111-1111-111111111111", testOrgP, "bot"),
		archivedServiceAccountFixture("22222222-2222-2222-2222-222222222222", testOrgP, "bot"),
	}})
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--org", testOrgP, "--project", "proj-1", "project", "service-account", "remove", "bot", "--yes")
	require.Equal(t, cerr.ExitUsage, code)
	require.Contains(t, stderr, "ambiguous")
	require.Contains(t, stderr, "11111111-1111-1111-1111-111111111111")
	require.Contains(t, stderr, "22222222-2222-2222-2222-222222222222")
}

func TestProjectServiceAccountRemove_byIDCanTargetArchivedReuse(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/"+testOrgP+"/service-accounts", http.StatusOK, map[string]any{"service_accounts": []map[string]any{
		serviceAccountFixture("11111111-1111-1111-1111-111111111111", testOrgP, "bot"),
		archivedServiceAccountFixture("22222222-2222-2222-2222-222222222222", testOrgP, "bot"),
	}})
	api.on(http.MethodDelete, "/projects/proj-1/service-accounts/22222222-2222-2222-2222-222222222222", http.StatusNoContent, "")
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--org", testOrgP, "--project", "proj-1", "project", "service-account", "remove", "22222222-2222-2222-2222-222222222222", "--yes")
	require.Equal(t, 0, code, stderr)
}

func TestKeyMint_interactiveProjectPickerCarriesPickedOrgIntoServiceAccountResolution(t *testing.T) {
	tty := true
	app.ForceTTY = &tty
	t.Cleanup(func() { app.ForceTTY = nil })
	prevSelect := selectPrompt
	selectCalls := 0
	selectPrompt = func(title string, opts []ui.Option) (string, error) {
		selectCalls++
		switch selectCalls {
		case 1:
			require.Equal(t, "Select an organization", title)
			return testOrgA, nil
		case 2:
			require.Equal(t, "Select a project", title)
			return testProjA1, nil
		default:
			return "", errors.New("unexpected extra picker: " + title)
		}
	}
	t.Cleanup(func() { selectPrompt = prevSelect })
	t.Setenv("CI", "")
	t.Setenv("DAYTONA_CONFIG_DIR", cfgDir(t))
	for _, key := range []string{"DAYTONA_API_URL", "DAYTONA_V2_API_URL", "DAYTONA_API_KEY", "DAYTONA_PROFILE", "DAYTONA_ORG", "DAYTONA_PROJECT", "DAYTONA_REGION", "DAYTONA_OIDC_CLIENT_ID", "DAYTONA_YES"} {
		t.Setenv(key, "")
	}
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs", http.StatusOK, organizationsFixture())
	api.on(http.MethodGet, "/orgs/"+testOrgA+"/projects", http.StatusOK, projectsFixture(testOrgA, "default"))
	api.on(http.MethodGet, "/orgs/"+testOrgA+"/service-accounts", http.StatusOK, map[string]any{"service_accounts": []map[string]any{serviceAccountFixture("11111111-1111-1111-1111-111111111111", testOrgA, "bot")}})
	api.on(http.MethodPost, "/projects/"+testProjA1+"/api-keys", http.StatusCreated, map[string]any{"id": "22222222-2222-2222-2222-222222222222", "name": "ci", "project_id": testProjA1, "created_at": "2026-09-01T00:00:00Z", "key_prefix": "dtn_abc", "key_suffix": "abc", "token": "dtn_token", "project_permissions": []string{"write:sandboxes"}, "sync_to_v0": false, "service_account_id": "11111111-1111-1111-1111-111111111111"})
	_, _, code := runNoEnv(t, "--api-url", api.srv.URL, "--api-key", "k", "key", "mint", "--name", "ci", "--permission", "write:sandboxes", "--service-account", "bot")
	require.Equal(t, 0, code)
	require.Equal(t, 2, selectCalls)
}

func TestKeyMint_serviceAccountNamePrefersLiveMatch(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/service-accounts", http.StatusOK, map[string]any{"service_accounts": []map[string]any{
		archivedServiceAccountFixture("11111111-1111-1111-1111-111111111111", testOrgP, "bot"),
		serviceAccountFixture("22222222-2222-2222-2222-222222222222", testOrgP, "bot"),
	}})
	api.on(http.MethodPost, "/projects/proj-1/api-keys", http.StatusCreated, map[string]any{"id": "33333333-3333-3333-3333-333333333333", "name": "ci", "project_id": testProjP1, "created_at": "2026-09-01T00:00:00Z", "key_prefix": "dtn_abc", "key_suffix": "abc", "token": "dtn_token", "project_permissions": []string{"write:sandboxes"}, "sync_to_v0": false, "service_account_id": "22222222-2222-2222-2222-222222222222"})
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "key", "mint", "--name", "ci", "--permission", "write:sandboxes", "--service-account", "bot")
	require.Equal(t, 0, code, stderr)
	mint := findRequest(t, api, http.MethodPost, "/projects/proj-1/api-keys")
	require.Equal(t, "22222222-2222-2222-2222-222222222222", mint.Body["service_account_id"])
}

func TestProjectMemberAdd_rejectsPositionalUserWhenBulkSet(t *testing.T) {
	api := newFakeAPI(t)
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "project", "member", "add", "alice", "--bulk", "bob")
	require.Equal(t, cerr.ExitUsage, code)
	require.Contains(t, stderr, "--bulk")
	require.Empty(t, api.requests)
}

func TestSandboxTimeout_rejectsNonPositiveValues(t *testing.T) {
	_, err := sandboxTimeout(&app.App{Globals: app.Globals{Timeout: "0s"}})
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	_, err = sandboxTimeout(&app.App{Globals: app.Globals{Timeout: "-1s"}})
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestConfigPath_usesStructuredPrinterOutput(t *testing.T) {
	stdout, stderr, code := run(t, cfgDir(t), nil, "-o", "json", "config", "path")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, mustJSON(t, stdout)["path"], "cfg")
}

func TestSandboxPreviewURL_escapesSandboxIDPathSegment(t *testing.T) {
	var paths []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		switch r.URL.Path {
		case "/sandboxes/a?b":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"id":"a?b","class":"linux","name":"box","status":"running","region_id":"region-b","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"spot":false,"port_map":[],"disk_mib":1024,"memory_mib":512,"cpu":1,"cores_max":1,"toolbox_proxy_url":"https://toolbox.example"}`))
		case "/sandboxes/a?b/ports/8080/preview-url":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"url":"https://preview.example","token":"tok"}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "sandbox", "port", "preview-url", "a?b", "8080")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "preview.example")
	require.Equal(t, []string{"/sandboxes/a?b", "/sandboxes/a?b/ports/8080/preview-url"}, paths)
}
