// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestKeyAndOrgKeyCommands_JSON(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/service-accounts", 200, map[string]any{"service_accounts": []map[string]any{serviceAccountFixture("11111111-1111-1111-1111-111111111111", testOrgP, "bot")}})
	f.on("POST", "/projects/proj-1/api-keys", 201, map[string]any{"id": "22222222-2222-2222-2222-222222222222", "name": "ci", "project_id": testProjP1, "created_at": "2026-09-01T00:00:00Z", "key_prefix": "dtn_abc", "key_suffix": "abc", "token": "dtn_token", "project_permissions": []string{"write:sandboxes"}, "sync_to_v0": false, "service_account_id": "11111111-1111-1111-1111-111111111111"})
	f.on("GET", "/projects/proj-1/api-keys", 200, map[string]any{"project_api_keys": []map[string]any{{"id": "22222222-2222-2222-2222-222222222222", "project_id": testProjP1, "name": "ci", "created_at": "2026-09-01T00:00:00Z", "project_permissions": []string{"write:sandboxes"}, "sync_to_v0": true}}})
	f.on("DELETE", "/projects/proj-1/api-keys/22222222-2222-2222-2222-222222222222", 204, "")
	f.on("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/api-keys", 201, map[string]any{"id": "33333333-3333-3333-3333-333333333333", "name": "audit", "org_id": testOrgP, "created_at": "2026-09-01T00:00:00Z", "key_prefix": "dtno_abc", "token": "dtno_token", "org_permissions": []string{"read:audit"}})
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/api-keys", 200, map[string]any{"organization_api_keys": []map[string]any{{"id": "33333333-3333-3333-3333-333333333333", "org_id": testOrgP, "name": "audit", "created_at": "2026-09-01T00:00:00Z", "org_permissions": []string{"read:audit"}}}})
	f.on("DELETE", "/orgs/0000000a-0000-4000-8000-00000000a001/api-keys/33333333-3333-3333-3333-333333333333", 204, "")

	out, _, code := runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "key", "mint", "--name", "ci", "--permission", "write:sandboxes", "--service-account", "bot")
	if code != 0 || mustJSON(t, out)["token"] != "dtn_token" {
		t.Fatalf("key mint: %d %s", code, out)
	}
	out, _, code = runMgmt(t, f, d, "--project", "proj-1", "key", "list")
	if code != 0 || mustJSON(t, out)["items"] == nil {
		t.Fatalf("key list: %d %s", code, out)
	}
	_, errb, code := runMgmt(t, f, d, "key", "revoke", "22222222-2222-2222-2222-222222222222")
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("guard: %d %s", code, errb)
	}
	out, _, code = runMgmt(t, f, d, "--project", "proj-1", "key", "revoke", "22222222-2222-2222-2222-222222222222", "--yes")
	if code != 0 || !isAck(t, out, "id", "22222222-2222-2222-2222-222222222222", "revoked") {
		t.Fatalf("key revoke: %d %s", code, out)
	}
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "org-key", "mint", "--name", "audit", "--permission", "read:audit")
	if code != 0 || mustJSON(t, out)["token"] != "dtno_token" {
		t.Fatalf("org-key mint: %d %s", code, out)
	}
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "org-key", "list")
	if code != 0 || mustJSON(t, out)["items"] == nil {
		t.Fatalf("org-key list: %d %s", code, out)
	}
	require.Equal(t, "limit=100", findRequest(t, f, http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/api-keys").Query)
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "org-key", "revoke", "33333333-3333-3333-3333-333333333333", "--yes")
	if code != 0 || !isAck(t, out, "id", "33333333-3333-3333-3333-333333333333", "revoked") {
		t.Fatalf("org-key revoke: %d %s", code, out)
	}
}

func TestServiceAccountProjectResourceBillingAndAuditCommands_JSON(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	pdfFile := filepath.Join(t.TempDir(), "invoice.pdf")
	createdServiceAccount := serviceAccountFixture("11111111-1111-1111-1111-111111111111", testOrgP, "bot")
	f.on("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/service-accounts", 201, createdServiceAccount)
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/service-accounts", 200, map[string]any{"service_accounts": []map[string]any{createdServiceAccount}})
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/service-accounts/11111111-1111-1111-1111-111111111111", 200, serviceAccountFixture("11111111-1111-1111-1111-111111111111", testOrgP, "bot"))
	updatedServiceAccount := serviceAccountFixture("11111111-1111-1111-1111-111111111111", testOrgP, "bot2")
	updatedServiceAccount["roles"] = []string{"auditor"}
	updatedServiceAccount["org_permissions"] = []string{"read:audit"}
	updatedServiceAccount["live_key_count"] = 1
	f.on("PATCH", "/orgs/0000000a-0000-4000-8000-00000000a001/service-accounts/11111111-1111-1111-1111-111111111111", 200, updatedServiceAccount)
	f.on("DELETE", "/orgs/0000000a-0000-4000-8000-00000000a001/service-accounts/11111111-1111-1111-1111-111111111111", 204, "")
	f.on("GET", "/projects/proj-1/service-accounts", 200, map[string]any{"service_accounts": []map[string]any{{"id": "11111111-1111-1111-1111-111111111111", "name": "bot", "implicit": false}}})
	f.on("POST", "/projects/proj-1/service-accounts", 201, map[string]any{"added_service_account_ids": []string{"11111111-1111-1111-1111-111111111111"}})
	f.on("DELETE", "/projects/proj-1/service-accounts/11111111-1111-1111-1111-111111111111", 204, "")
	f.on("PUT", "/secrets/API_KEY", 200, map[string]any{"name": "API_KEY", "allowed_domains": []string{"api.example.com"}, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	f.on("GET", "/secrets", 200, map[string]any{"secrets": []map[string]any{{"name": "API_KEY", "allowed_domains": []string{}, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"}}})
	f.on("GET", "/secrets/API_KEY", 200, map[string]any{"name": "API_KEY", "allowed_domains": []string{}, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	f.on("DELETE", "/secrets/API_KEY", 204, "")
	f.on("PUT", "/registries/ghcr.io", 200, map[string]any{"host": "ghcr.io", "kind": "basic", "username": "me", "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	f.on("GET", "/registries", 200, map[string]any{"registries": []map[string]any{{"host": "ghcr.io", "kind": "basic", "username": "me", "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"}}})
	f.on("GET", "/registries/ghcr.io", 200, map[string]any{"host": "ghcr.io", "kind": "basic", "username": "me", "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"})
	f.on("DELETE", "/registries/ghcr.io", 204, "")
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/billing/wallet", 200, walletFixture())
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/billing/usage-periods", 200, pastUsageFixture())
	f.on("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/billing/top-up-sessions", 201, map[string]any{"url": "https://checkout.example", "expires_at": nil})
	f.on("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/billing/portal-sessions", 201, map[string]any{"url": "https://portal.example", "expires_at": nil})
	f.on("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/billing/invoices/inv-1/payment-sessions", 201, map[string]any{"url": "https://invoice.example", "expires_at": nil})
	f.handle("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/billing/invoices/inv-1/pdf", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/pdf")
		_, _ = w.Write([]byte("PDF"))
	})
	f.handle("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/audit-logs", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("cursor") == "c2" {
			_, _ = w.Write([]byte(`{"audit_logs":[{"action":"second","actor_kind":"user","created_at":"2026-09-01T00:00:01Z","id":"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb","metadata":{},"org_id":"` + testOrgP + `","status_code":200,"target_type":"sandbox"}]}`))
			return
		}
		_, _ = w.Write([]byte(`{"next_cursor":"c2","audit_logs":[{"action":"first","actor_kind":"user","created_at":"2026-09-01T00:00:00Z","id":"cccccccc-cccc-cccc-cccc-cccccccccccc","metadata":{},"org_id":"` + testOrgP + `","status_code":200,"target_type":"sandbox"}]}`))
	})

	checks := [][]string{
		{"--org", "0000000a-0000-4000-8000-00000000a001", "service-account", "create", "bot", "--role", "developer"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "service-account", "list"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "service-account", "get", "11111111-1111-1111-1111-111111111111"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "service-account", "update", "11111111-1111-1111-1111-111111111111", "--name", "bot2", "--role", "auditor"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "project", "service-account", "list"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "project", "service-account", "add", "bot"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "secret", "set", "API_KEY", "--value", "secret", "--allowed-domain", "api.example.com"},
		{"--project", "proj-1", "secret", "list"},
		{"--project", "proj-1", "secret", "get", "API_KEY"},
		{"--project", "proj-1", "registry", "set", "ghcr.io", "--username", "me", "--password", "pw"},
		{"--project", "proj-1", "registry", "list"},
		{"--project", "proj-1", "registry", "get", "ghcr.io"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "billing", "wallet"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "billing", "usage-periods", "--limit", "1"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "billing", "top-up", "--amount-cents", "2500"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "billing", "portal-session"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "billing", "invoice", "payment-session", "inv-1"},
		{"--org", "0000000a-0000-4000-8000-00000000a001", "audit", "list", "--all", "--action", "sandbox.create"},
	}
	for _, args := range checks {
		out, errb, code := runMgmt(t, f, d, args...)
		if code != 0 || out == "" {
			t.Fatalf("%v => %d %s %s", args, code, out, errb)
		}
	}
	_, errb, code := runMgmt(t, f, d, "service-account", "delete", "11111111-1111-1111-1111-111111111111")
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("sa guard: %d %s", code, errb)
	}
	_, errb, code = runMgmt(t, f, d, "--project", "proj-1", "secret", "delete", "API_KEY")
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("secret guard: %d %s", code, errb)
	}
	_, errb, code = runMgmt(t, f, d, "--project", "proj-1", "registry", "delete", "ghcr.io")
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("registry guard: %d %s", code, errb)
	}
	out, _, code := runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "service-account", "delete", "11111111-1111-1111-1111-111111111111", "--yes")
	if code != 0 || !isAck(t, out, "id", "11111111-1111-1111-1111-111111111111", "archived") {
		t.Fatalf("sa delete: %d %s", code, out)
	}
	// Acknowledgements: the identifier under the field that names it, plus
	// a boolean named after the action.
	acks := []struct {
		args                []string
		idField, id, action string
	}{
		{[]string{"--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "project", "service-account", "remove", "bot", "--yes"}, "id", "11111111-1111-1111-1111-111111111111", "removed"},
		{[]string{"--project", "proj-1", "secret", "delete", "API_KEY", "--yes"}, "name", "API_KEY", "deleted"},
		{[]string{"--project", "proj-1", "registry", "delete", "ghcr.io", "--yes"}, "host", "ghcr.io", "deleted"},
	}
	for _, ack := range acks {
		out, errb, code := runMgmt(t, f, d, ack.args...)
		if code != 0 || !isAck(t, out, ack.idField, ack.id, ack.action) {
			t.Fatalf("%v => %d %s %s", ack.args, code, out, errb)
		}
	}
	// set prints the resource the PUT returns, as get does.
	out, errb, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "secret", "set", "API_KEY", "--value", "secret", "--allowed-domain", "api.example.com")
	if code != 0 || mustJSON(t, out)["name"] != "API_KEY" || mustJSON(t, out)["created_at"] == nil {
		t.Fatalf("secret set: %d %s %s", code, out, errb)
	}
	out, errb, code = runMgmt(t, f, d, "--project", "proj-1", "registry", "set", "ghcr.io", "--username", "me", "--password", "pw")
	if code != 0 || mustJSON(t, out)["host"] != "ghcr.io" || mustJSON(t, out)["kind"] != "basic" {
		t.Fatalf("registry set: %d %s %s", code, out, errb)
	}
	_, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "invoice", "pdf", "inv-1", "--out", pdfFile)
	if code != 0 {
		t.Fatalf("invoice pdf: %d", code)
	}
	if body, err := os.ReadFile(pdfFile); err != nil || string(body) != "PDF" {
		t.Fatalf("pdf file: %v %q", err, string(body))
	}
}
