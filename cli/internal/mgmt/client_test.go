// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package mgmt

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	api "github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/google/uuid"
	openapi_types "github.com/oapi-codegen/runtime/types"
)

func TestResolveOrg(t *testing.T) {
	orgs := []gen.OrganizationListItem{{Id: mustUUID(t, "11111111-1111-1111-1111-111111111111"), Name: "Acme"}, {Id: mustUUID(t, "22222222-2222-2222-2222-222222222222"), Name: "acme"}, {Id: mustUUID(t, "33333333-3333-3333-3333-333333333333"), Name: "Solo", Personal: true}}
	if o, _ := ResolveOrg(orgs, "33333333-3333-3333-3333-333333333333"); o == nil || o.Name != "Solo" {
		t.Fatal("by id")
	}
	if o, err := ResolveOrg(orgs, strings.ToUpper("33333333-3333-3333-3333-333333333333")); err != nil || o == nil || o.Name != "Solo" {
		t.Fatalf("upper-case id lookup failed: %v %v", o, err)
	}
	if o, _ := ResolveOrg(orgs, "solo"); o == nil || o.Id.String() != "33333333-3333-3333-3333-333333333333" {
		t.Fatal("by name ci")
	}
	if _, err := ResolveOrg(orgs, "ACME"); cerr.ExitCode(err) != cerr.ExitUsage {
		t.Fatalf("case-insensitive tie should be ambiguous: %v", err)
	}
	if o, err := ResolveOrg(orgs, "Acme"); err != nil || o.Id.String() != "11111111-1111-1111-1111-111111111111" {
		t.Fatalf("exact case must win over a folded tie: %v %v", o, err)
	}
	if o, err := ResolveOrg(orgs, "acme"); err != nil || o.Id.String() != "22222222-2222-2222-2222-222222222222" {
		t.Fatalf("exact case must win over a folded tie: %v %v", o, err)
	}
	if _, err := ResolveOrg(orgs, "nope"); cerr.ExitCode(err) != cerr.ExitNotFound {
		t.Fatal(err)
	}
}

func TestDefaultProject(t *testing.T) {
	ps := []gen.Project{{Id: mustUUID(t, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), Name: "x"}, {Id: mustUUID(t, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), Name: "default", Default: true}}
	if DefaultProject(ps).Id.String() != "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" {
		t.Fatal()
	}
	byMarker := []gen.Project{{Id: mustUUID(t, "cccccccc-cccc-cccc-cccc-cccccccccccc"), Name: "sandbox", Default: true}, {Id: mustUUID(t, "dddddddd-dddd-dddd-dddd-dddddddddddd"), Name: "default"}}
	if DefaultProject(byMarker).Id.String() != "cccccccc-cccc-cccc-cccc-cccccccccccc" {
		t.Fatal("default marker must win over the name fallback")
	}
	if DefaultProject(ps[:1]).Id.String() != "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" {
		t.Fatal("fallback to first")
	}
	if DefaultProject(nil) != nil {
		t.Fatal()
	}
	if p, err := ResolveProject(ps, strings.ToUpper("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")); err != nil || p == nil || p.Name != "x" {
		t.Fatalf("upper-case project id lookup failed: %v %v", p, err)
	}
	if _, err := ResolveProject(ps, "X"); err != nil {
		t.Fatal(err)
	}
}

func TestListOrgs_decodesEnvelope(t *testing.T) {
	// The fixture is built from the GENERATED contract types, not a hand-written
	// map: a field the contract adds or drops is a compile error here, so this
	// test cannot stay green against a stale shape (the cmd package's fixture
	// server validates bodies against contract/api-openapi.json at
	// runtime; this package has no such server, so the type system is the gate).
	fixture := gen.OrganizationPage{Organizations: []gen.OrganizationListItem{{
		Id:       openapi_types.UUID(uuid.MustParse("44444444-4444-4444-4444-444444444444")),
		Name:     "Acme",
		Personal: false,
		Membership: gen.OrganizationMembership{
			DefaultProjectId:            openapi_types.UUID(uuid.MustParse("55555555-5555-5555-5555-555555555555")),
			Roles:                       []string{"admin"},
			Permissions:                 []string{},
			GrantableProjectPermissions: []string{},
		},
		Suspended:              false,
		PreviewWarningDisabled: false,
	}}}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(fixture)
	}))
	defer server.Close()
	items, err := ListOrgs(t.Context(), api.New(server.URL, "k", "test"))
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 || items[0].Name != "Acme" {
		t.Fatalf("items=%+v", items)
	}
}

func TestCreateKey_delegatesToCreateProjectKey(t *testing.T) {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	fset := token.NewFileSet()
	parsed, err := parser.ParseFile(fset, filepath.Join(filepath.Dir(file), "client.go"), nil, 0)
	if err != nil {
		t.Fatalf("parse client.go: %v", err)
	}
	var found bool
	for _, decl := range parsed.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Name.Name != "CreateKey" {
			continue
		}
		found = true
		if len(fn.Body.List) != 1 {
			t.Fatalf("CreateKey should contain exactly one statement, got %d", len(fn.Body.List))
		}
		ret, ok := fn.Body.List[0].(*ast.ReturnStmt)
		if !ok || len(ret.Results) != 1 {
			t.Fatalf("CreateKey should be a single return call")
		}
		call, ok := ret.Results[0].(*ast.CallExpr)
		if !ok {
			t.Fatalf("CreateKey return should call CreateProjectKey")
		}
		ident, ok := call.Fun.(*ast.Ident)
		if !ok || ident.Name != "CreateProjectKey" {
			t.Fatalf("CreateKey should delegate to CreateProjectKey, got %#v", call.Fun)
		}
	}
	if !found {
		t.Fatal("CreateKey function not found")
	}
}

func TestOrganizationResponse_fixtureRoundTrips(t *testing.T) {
	raw := []byte(`{"id":"44444444-4444-4444-4444-444444444444","name":"Acme","personal":false,"from_v0":true,"suspended":true,"suspended_reason":"example reason","preview_warning_disabled":false,"v0_membership_sync_enabled":true,"sso_linked":true,"sso_organization_id":"org_01ACME","budget_stop_grace_seconds":3600,"created_at":"2026-09-01T00:00:00Z","updated_at":"2026-09-01T00:00:00Z"}`)
	var org gen.Organization
	if err := json.Unmarshal(raw, &org); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	encoded, err := json.Marshal(org)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if decoded["id"] != "44444444-4444-4444-4444-444444444444" || decoded["suspended_reason"] != "example reason" || decoded["from_v0"] != true || decoded["v0_membership_sync_enabled"] != true || decoded["sso_linked"] != true {
		t.Fatalf("round-trip lost fields: %#v", decoded)
	}
}

func mustUUID(t *testing.T, value string) openapi_types.UUID {
	t.Helper()
	var id openapi_types.UUID
	if err := id.UnmarshalText([]byte(value)); err != nil {
		t.Fatalf("uuid %s: %v", value, err)
	}
	return id
}
