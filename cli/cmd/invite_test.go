// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"

	gen "github.com/daytona/clients/cli/gen/api"
)

func TestInviteStatus(t *testing.T) {
	past := time.Now().Add(-time.Hour)
	future := time.Now().Add(time.Hour)
	declined := time.Date(2026, 9, 13, 0, 0, 0, 0, time.UTC)
	cases := []struct {
		name string
		item gen.Invitation
		want string
	}{
		{"live", gen.Invitation{ExpiresAt: future}, "pending"},
		{"expired", gen.Invitation{ExpiresAt: past}, "expired"},
		{"declined outranks expiry", gen.Invitation{ExpiresAt: past, DeclinedAt: &declined}, "declined"},
		{"without expires_at", gen.Invitation{}, "pending"},
	}
	for _, c := range cases {
		if got := inviteStatus(c.item); got != c.want {
			t.Errorf("%s: got %q, want %q", c.name, got, c.want)
		}
	}
}

const (
	testInviteToken = "dtni_c3VwZXJzZWNyZXR0b2tlbnZhbHVlMDEyMzQ1Njc4OTB" // dtni_ + 43 base64url chars, the contract shape
	testInviteLink  = "https://app.example.com/dashboard/user/invitations?token=" + testInviteToken
)

func TestInvitationTokenArg(t *testing.T) {
	cases := []struct {
		name, arg, want string
		usage           bool
	}{
		{"bare token", testInviteToken, testInviteToken, false},
		{"bare token with whitespace", "  " + testInviteToken + "\n", testInviteToken, false},
		{"link", testInviteLink, testInviteToken, false},
		{"link with other params", "https://app.example.com/dashboard/user/invitations?utm=x&token=" + testInviteToken + "#top", testInviteToken, false},
		{"relative link from invite create", "/dashboard/user/invitations?token=" + testInviteToken, testInviteToken, false},
		// Mangled copies of the link: none may fall through to the api as
		// the token.
		{"scheme-less link", "app.example.com/dashboard/user/invitations?token=" + testInviteToken, testInviteToken, false},
		{"autolink in angle brackets", "<" + testInviteLink + ">", testInviteToken, false},
		{"double-quoted link", `"` + testInviteLink + `"`, testInviteToken, false},
		{"single-quoted link", "'" + testInviteLink + "'", testInviteToken, false},
		{"quoted autolink", `"<` + testInviteLink + `>"`, testInviteToken, false},
		{"link with a bad port", "https://app.example.com:bad/dashboard/user/invitations?token=" + testInviteToken, testInviteToken, false},
		{"hash-router link", "https://app.example.com/#/invitations?token=" + testInviteToken, testInviteToken, false},
		{"quoted bare token", `"` + testInviteToken + `"`, testInviteToken, false},
		{"relative link without token", "/dashboard/user/invitations", "", true},
		{"link without token", "https://app.example.com/dashboard/user/invitations", "", true},
		{"link with empty token", "https://app.example.com/dashboard/user/invitations?token=", "", true},
		{"link with a mangled token", testInviteLink + "x", "", true},
		{"empty", "", "", true},
		{"whitespace", "   ", "", true},
		{"empty quotes", `""`, "", true},
		// Not the contract shape (dtni_ + exactly 43 base64url chars): refused
		// before any request instead of a 404 from the api.
		{"44-char token", testInviteToken + "x", "", true},
		{"42-char token", testInviteToken[:len(testInviteToken)-1], "", true},
		{"wrong prefix", "dtna_" + testInviteToken[5:], "", true},
		{"token with ?", testInviteToken + "?", "", true},
		{"token with whitespace inside", testInviteToken[:10] + " " + testInviteToken[11:], "", true},
		{"token with a non-base64url char", testInviteToken[:10] + "+" + testInviteToken[11:], "", true},
		{"invitation id instead of token", "22222222-2222-2222-2222-222222222222", "", true},
	}
	for _, c := range cases {
		got, err := invitationTokenArg(c.arg)
		if c.usage {
			if err == nil || cerr.ExitCode(err) != cerr.ExitUsage || !has(err.Error(), "invitation token or link required") {
				t.Errorf("%s: want usage error, got %q, %v", c.name, got, err)
			}
			continue
		}
		if err != nil || got != c.want {
			t.Errorf("%s: got %q, %v; want %q", c.name, got, err, c.want)
		}
	}
}

func TestInviteAndRoleCommands_JSON(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/invitations", 201, invitationFixture("11111111-1111-1111-1111-111111111111", testOrgP, "new@example.com"))
	listedInvitation := invitationFixture("11111111-1111-1111-1111-111111111111", testOrgP, "new@example.com")
	listedInvitation["status"] = "declined"
	listedInvitation["declined_at"] = "2026-09-13T00:00:00Z"
	f.on("GET", "/orgs/0000000a-0000-4000-8000-00000000a001/invitations", 200, map[string]any{"invitations": []map[string]any{listedInvitation}})
	f.on("DELETE", "/orgs/0000000a-0000-4000-8000-00000000a001/invitations/11111111-1111-1111-1111-111111111111", 204, "")
	// The invitee routes take the token in the body only (never the path):
	// every fixture refuses any other body, so an incorrect
	// `/invitations/{id}/…` shape or a missing token fails here, on top of
	// the contract check.
	wantToken := func(w http.ResponseWriter, r *http.Request, ok string) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if len(body) != 1 || body["token"] != testInviteToken {
			w.WriteHeader(404)
			_, _ = w.Write([]byte(`{"code":"NOT_FOUND","message":"Invitation not found","source":"DAYTONA_API"}`))
			return
		}
		_, _ = w.Write([]byte(ok))
	}
	f.handle("POST", "/invitations/lookup", func(w http.ResponseWriter, r *http.Request) {
		wantToken(w, r, `{"id":"22222222-2222-2222-2222-222222222222","org_id":"33333333-3333-3333-3333-333333333333","org_name":"Acme","roles":["developer"],"invited_by":"Alice","email":"new@example.com","expires_at":"2026-10-20T00:00:00Z"}`)
	})
	f.handle("POST", "/invitations/accept", func(w http.ResponseWriter, r *http.Request) {
		wantToken(w, r, fixtureJSON(organizationMemberFixture("33333333-3333-3333-3333-333333333333", "new@example.com", "New User")))
	})
	f.handle("POST", "/invitations/decline", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if len(body) != 1 || body["token"] != testInviteToken {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
	f.on("GET", "/roles", 200, map[string]any{"roles": []map[string]any{{"id": "developer", "name": "Developer", "description": "Dev", "permissions": []string{"read:members"}}}})

	out, _, code := runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "invite", "create", "new@example.com", "--role", "developer")
	if code != 0 || mustJSON(t, out)["id"] != "11111111-1111-1111-1111-111111111111" {
		t.Fatalf("create: %d %s", code, out)
	}
	if _, present := mustJSON(t, out)["accept_url"]; present {
		t.Fatalf("create without accept_url must not invent one: %s", out)
	}
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "invite", "list")
	if code != 0 || mustJSON(t, out)["items"] == nil {
		t.Fatalf("list: %d %s", code, out)
	}
	_, errb, code := runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "invite", "revoke", "11111111-1111-1111-1111-111111111111")
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("guard: %d %s", code, errb)
	}
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "invite", "revoke", "11111111-1111-1111-1111-111111111111", "--yes")
	if code != 0 || !isAck(t, out, "id", "11111111-1111-1111-1111-111111111111", "revoked") {
		t.Fatalf("revoke: %d %s", code, out)
	}
	// show takes the bare token; accept the emailed link; decline the token
	// again — the fake accepts exactly one body for all three.
	out, _, code = runMgmt(t, f, d, "invite", "get", testInviteToken)
	if code != 0 || mustJSON(t, out)["org_name"] != "Acme" || mustJSON(t, out)["invited_by"] != "Alice" {
		t.Fatalf("show: %d %s", code, out)
	}
	if got := findRequest(t, f, "POST", "/invitations/lookup"); got.Query != "" || got.Body["token"] != testInviteToken {
		t.Fatalf("lookup must carry the token in the body only: %+v", got)
	}
	out, _, code = runMgmt(t, f, d, "invite", "accept", testInviteLink)
	if code != 0 || mustJSON(t, out)["user_id"] != "33333333-3333-3333-3333-333333333333" || mustJSON(t, out)["roles"] == nil {
		t.Fatalf("accept: %d %s", code, out)
	}
	if got := findRequest(t, f, "POST", "/invitations/accept"); got.Query != "" || got.Body["token"] != testInviteToken {
		t.Fatalf("accept must carry the token from the link in the body only: %+v", got)
	}
	out, _, code = runMgmt(t, f, d, "invite", "decline", testInviteToken)
	if code != 0 || mustJSON(t, out)["declined"] != true {
		t.Fatalf("decline: %d %s", code, out)
	}
	if got := findRequest(t, f, "POST", "/invitations/decline"); got.Body["token"] != testInviteToken {
		t.Fatalf("decline must carry the token in the body: %+v", got)
	}
	// A link without a token is refused before any request is made.
	before := len(f.requests)
	_, errb, code = runMgmt(t, f, d, "invite", "accept", "https://app.example.com/dashboard/user/invitations")
	if code != cerr.ExitUsage || !has(errb, "invitation token or link required") || len(f.requests) != before {
		t.Fatalf("accept without token: %d %s (requests %d -> %d)", code, errb, before, len(f.requests))
	}
	// A bare token that is not the contract shape is refused the same way.
	before = len(f.requests)
	_, errb, code = runMgmt(t, f, d, "invite", "get", testInviteToken+"x")
	if code != cerr.ExitUsage || !has(errb, "invitation token or link required") || len(f.requests) != before {
		t.Fatalf("show with a malformed token: %d %s (requests %d -> %d)", code, errb, before, len(f.requests))
	}
	out, _, code = runMgmt(t, f, d, "role", "list")
	if code != 0 {
		t.Fatalf("roles: %d %s", code, out)
	}
	if roles, ok := mustJSON(t, out)["items"].([]any); !ok || len(roles) == 0 {
		t.Fatalf("roles: %d %s", code, out)
	}
}

// When the response includes accept_url, human output prints the invitation
// link on its own line and JSON preserves it.
func TestInviteCreatePrintsAcceptURL(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/invitations", 201, invitationWithAcceptURLFixture(testInviteLink))

	out, errb, code := run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "k", "-o", "table", "--org", "0000000a-0000-4000-8000-00000000a001", "invite", "create", "new@example.com")
	if code != 0 {
		t.Fatalf("create: %d %s", code, errb)
	}
	if !has(out, "Invitation link: "+testInviteLink+"\n") || !has(out, "No invitation email is sent by this deployment; share the link with the invitee.") {
		t.Fatalf("human output must print the link and the hint:\n%s", out)
	}
	if !has(out, "new@example.com") || !has(out, "2026-10-20T00:00:00Z") {
		t.Fatalf("human output must still render the invitation rows:\n%s", out)
	}
	out, _, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "invite", "create", "new@example.com")
	if code != 0 || mustJSON(t, out)["accept_url"] != testInviteLink || has(out, "Invitation link:") {
		t.Fatalf("json output must carry accept_url in the DTO only: %d %s", code, out)
	}
}

func TestInviteCreatePrintsTheTokenForARelativeAcceptURL(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.on("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/invitations", 201, invitationWithAcceptURLFixture("/dashboard/user/invitations?token="+testInviteToken))
	out, errb, code := run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "k", "-o", "table", "--org", "0000000a-0000-4000-8000-00000000a001", "invite", "create", "new@example.com")
	if code != 0 {
		t.Fatalf("create: %d %s", code, errb)
	}
	if !has(out, "Invitation token: "+testInviteToken+"\n") || !has(out, "No invitation email is sent by this deployment; give the invitee this token for daytona invite accept <token>.") || has(out, "Invitation link:") {
		t.Fatalf("a relative accept_url must print the token, not a host-less link: %d %s", code, out)
	}
}

func TestInviteCreateFallsBackToTheLinkWhenARelativeTokenIsMalformed(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	rel := "/dashboard/user/invitations?token=dtni_short"
	f.on("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/invitations", 201, invitationWithAcceptURLFixture(rel))
	out, errb, code := run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "k", "-o", "table", "--org", "0000000a-0000-4000-8000-00000000a001", "invite", "create", "new@example.com")
	if code != 0 {
		t.Fatalf("create: %d %s", code, errb)
	}
	if !has(out, "Invitation link: "+rel+"\n") || has(out, "Invitation token:") {
		t.Fatalf("a relative link whose token fails the shape gate must still be printed as the link: %s", out)
	}
}

// Omitting --role leaves `roles` out of the body so the server's documented
// default (`developer`) applies; an explicit empty --role is a usage error.
func TestInviteCreateWithoutRoleUsesServerDefault(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	f.handle("POST", "/orgs/0000000a-0000-4000-8000-00000000a001/invitations", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if _, present := body["roles"]; present {
			w.WriteHeader(400)
			_, _ = w.Write([]byte(`{"code":"BAD_REQUEST","message":"roles must be omitted for the default","source":"DAYTONA_API"}`))
			return
		}
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(fixtureJSON(invitationFixture("0000000b-0000-4000-8000-00000000b001", testOrgP, "new@example.com"))))
	})
	_, errb, code := runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "invite", "create", "new@example.com")
	if code != 0 {
		t.Fatalf("%d %s", code, errb)
	}
	_, errb, code = runMgmt(t, f, d, "--org", "0000000a-0000-4000-8000-00000000a001", "invite", "create", "new@example.com", "--role", "")
	if code != cerr.ExitUsage {
		t.Fatalf("explicit empty --role must be a usage error: %d %s", code, errb)
	}
}

func TestInviteRevokePreflightRunsBeforeOrgResolution(t *testing.T) {
	_, errb, code := run(t, cfgDir(t), nil, "invite", "revoke", "11111111-1111-1111-1111-111111111111", "--api-url", "http://127.0.0.1:1", "--api-key", "k")
	if code != cerr.ExitUsage || !has(errb, "--yes") {
		t.Fatalf("%d %s", code, errb)
	}
}

// An unknown invite subcommand is a usage error without a request; invoking
// the parent without a subcommand prints help successfully.
func TestInviteParentRefusesUnknownSubcommand(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	out, errb, code := run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "k", "invite", "unknown-subcommand")
	if code != cerr.ExitUsage || !has(errb, `unknown command "unknown-subcommand" for "daytona invite"`) || len(f.requests) != 0 {
		t.Fatalf("unknown invite subcommand: %d stdout=%q stderr=%q (requests %d)", code, out, errb, len(f.requests))
	}
	out, errb, code = run(t, d, nil, "--api-url", f.srv.URL, "--api-key", "k", "invite")
	if code != 0 || !has(out, "Usage:") || !has(out, "accept") || len(f.requests) != 0 {
		t.Fatalf("bare invite must print help: %d stdout=%q stderr=%q (requests %d)", code, out, errb, len(f.requests))
	}
}
