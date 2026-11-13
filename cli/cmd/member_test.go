// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func runMgmt(t *testing.T, f *fakeAPI, d string, args ...string) (string, string, int) {
	t.Helper()
	base := []string{"--api-url", f.srv.URL, "--api-key", "k", "-o", "json"}
	return run(t, d, nil, append(base, args...)...)
}

func TestMemberCommands_JSONAndYesGuard(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/members", 200, map[string]any{"members": []map[string]any{organizationMemberFixture(testUserID, "a@example.com", "Alice")}})
	f.on("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/members/"+testUserID+"/update-preview", 200, map[string]any{"revoked_api_keys": 2})
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/members/"+testUserID+"/removal-preview", 200, map[string]any{"user_id": testUserID, "revoked_api_keys": 3})
	f.on("DELETE", "/orgs/0000000a-0000-4000-8000-00000000a001/members/"+testUserID, 204, "")

	out, errb, code := runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "member", "list")
	if code != 0 || mustJSON(t, out)["items"] == nil || errb != "" {
		t.Fatalf("list: %d %s %s", code, out, errb)
	}
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "member", "update", "a@example.com", "--role", "developer,auditor", "--dry-run")
	if code != 0 || mustJSON(t, out)["revoked_api_keys"].(float64) != 2 {
		t.Fatalf("update: %d %s", code, out)
	}
	if f.requests[2].Method != http.MethodPost || f.requests[2].Path != "/orgs/0000000a-0000-4000-8000-00000000a001/members/"+testUserID+"/update-preview" || f.requests[2].Body["roles"] == nil {
		t.Fatalf("preview request: %+v", f.requests[2])
	}
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "member", "remove", testUserID, "--dry-run")
	if code != 0 || mustJSON(t, out)["revoked_api_keys"].(float64) != 3 {
		t.Fatalf("preview: %d %s", code, out)
	}
	_, errb, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "member", "remove", testUserID)
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("guard: %d %s", code, errb)
	}
	deleteCount := countRequests(f.requests, http.MethodDelete, "/orgs/0000000a-0000-4000-8000-00000000a001/members/"+testUserID)
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "member", "remove", testUserID, "--yes")
	if code != 0 || !isAck(t, out, "user_id", testUserID, "removed") {
		t.Fatalf("remove: %d %s", code, out)
	}
	if countRequests(f.requests, http.MethodDelete, "/orgs/0000000a-0000-4000-8000-00000000a001/members/"+testUserID) != deleteCount+1 {
		t.Fatalf("delete not issued once: %+v", f.requests)
	}
}

func TestMemberUpdate_withoutDryRunUsesCanonicalMemberResponse(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/members", 200, map[string]any{"members": []map[string]any{organizationMemberFixture(testUserID, "a@example.com", "Alice")}})
	f.on("PATCH", "/orgs/0000000a-0000-4000-8000-00000000a001/members/"+testUserID, 200, organizationMemberFixture(testUserID, "a@example.com", "Alice"))

	out, errb, code := runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "member", "update", "a@example.com", "--role", "developer")
	if code != 0 || mustJSON(t, out)["user_id"] != testUserID || mustJSON(t, out)["roles"] == nil {
		t.Fatalf("%d %s %s", code, out, errb)
	}
}

func TestMemberRemoveSelfResolutionAvoidsListing(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("DELETE", "/orgs/0000000a-0000-4000-8000-00000000a001/members/"+testUserID, 204, "")
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@example.com"})

	_, errb, code := runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "member", "remove", testUserID, "--yes")
	if code != 0 {
		t.Fatalf("uuid self-remove: %d %s", code, errb)
	}
	if countRequests(f.requests, http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/members") != 0 {
		t.Fatalf("uuid removal must not list members: %+v", f.requests)
	}

	_, errb, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "member", "remove", "me", "--yes")
	if code != 0 {
		t.Fatalf("me self-remove: %d %s", code, errb)
	}
	if countRequests(f.requests, http.MethodGet, "/me") != 1 {
		t.Fatalf("me removal must resolve via GET /me exactly once: %+v", f.requests)
	}
	if countRequests(f.requests, http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/members") != 0 {
		t.Fatalf("me removal must not list members: %+v", f.requests)
	}
}

func countRequests(reqs []recorded, method string, path string) int {
	count := 0
	for _, req := range reqs {
		if req.Method == method && req.Path == path {
			count++
		}
	}
	return count
}

func TestProjectMemberCommands_JSON(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/members", 200, map[string]any{"members": []map[string]any{organizationMemberFixture(testUserID, "a@example.com", "Alice"), organizationMemberFixture("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "b@example.com", "Bob")}})
	f.on("GET", "/projects/proj-1/members", 200, map[string]any{"members": []map[string]any{{"user_id": testUserID, "email": "a@example.com", "name": "Alice", "implicit": false}}})
	f.on("POST", "/projects/proj-1/members", 201, map[string]any{"added_user_ids": []string{testUserID}})
	f.on("DELETE", "/projects/proj-1/members/"+testUserID, 204, "")

	out, _, code := runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "project", "member", "list")
	if code != 0 || mustJSON(t, out)["items"] == nil {
		t.Fatalf("list: %d %s", code, out)
	}
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "project", "member", "add", "a@example.com")
	if add := findRequest(t, f, http.MethodPost, "/projects/proj-1/members"); code != 0 || add.Body["user_ids"] == nil {
		t.Fatalf("add: %d %+v", code, add)
	}
	require.Contains(t, mustJSON(t, out), "added_user_ids")
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "project", "member", "add", "--bulk", "a@example.com,b@example.com")
	if code != 0 {
		t.Fatalf("bulk: %d", code)
	}
	findRequest(t, f, http.MethodPost, "/projects/proj-1/members")
	_, errb, code := runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "project", "member", "remove", testUserID)
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("guard: %d %s", code, errb)
	}
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "--project", "proj-1", "project", "member", "remove", testUserID, "--yes")
	if code != 0 || !isAck(t, out, "user_id", testUserID, "removed") {
		t.Fatalf("remove: %d %s", code, out)
	}
}

func TestMemberDryRunTablesShowOnlyTheRevokedAPIKeyCount(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/members", 200, map[string]any{"members": []map[string]any{organizationMemberFixture(testUserID, "a@example.com", "Alice")}})
	f.on("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/members/"+testUserID+"/update-preview", 200, map[string]any{"revoked_api_keys": 2})
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/members/"+testUserID+"/removal-preview", 200, map[string]any{"user_id": testUserID, "revoked_api_keys": 3})
	base := []string{"--api-url", f.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "member"}
	for _, args := range [][]string{{"update", "a@example.com", "--role", "developer", "--dry-run"}, {"remove", testUserID, "--dry-run"}} {
		out, errb, code := run(t, d, nil, append(append([]string{}, base...), args...)...)
		if code != 0 || !has(out, "revoked_api_keys") || has(out, "preview_grants") {
			t.Fatalf("%v: %d %q %q", args, code, out, errb)
		}
	}
}
