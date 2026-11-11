// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"strings"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func projectFixture(id, org, name string) map[string]any {
	return map[string]any{"id": id, "org_id": org, "name": name, "default": name == "default", "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"}
}

func TestCheckOrgProjectName(t *testing.T) {
	for _, tc := range []struct {
		kind, name, want string
		tooLong          bool
	}{
		{kind: "organization", name: "Acme"},
		{kind: "organization", name: "  " + strings.Repeat("a", 64) + "  "},
		{kind: "project", name: strings.Repeat("é", 64)},
		{kind: "organization", name: "default"},
		{kind: "project", name: "Default"},
		{kind: "organization", name: "", want: "must not be blank"},
		{kind: "project", name: " \t ", want: "must not be blank"},
		{kind: "organization", name: "a\x00b", want: "control characters"},
		{kind: "project", name: "lab\nnext", want: "control characters"},
		{kind: "project", name: "a\u0085b", want: "control characters"},
		{kind: "project", name: " default ", want: "reserved"},
		{kind: "organization", name: strings.Repeat("a", 65), want: "at most 64 characters (got 65)", tooLong: true},
		{kind: "project", name: strings.Repeat("é", 65), want: "at most 64 characters (got 65)", tooLong: true},
	} {
		tooLong, err := checkOrgProjectName(tc.kind, tc.name)
		switch {
		case tc.want == "":
			require.NoError(t, err, "%q", tc.name)
			require.NoError(t, tooLong, "%q", tc.name)
		case tc.tooLong:
			require.NoError(t, err, "%q", tc.name)
			require.ErrorContains(t, tooLong, tc.want, "%q", tc.name)
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(tooLong))
			require.ErrorContains(t, checkNewOrgProjectName(tc.kind, tc.name), tc.want)
		default:
			require.NoError(t, tooLong, "%q", tc.name)
			require.ErrorContains(t, err, tc.want, "%q", tc.name)
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
		}
	}
}

// An invalid name is refused before any request: no credential, no lookup.
func TestOrgProjectCreate_refusesInvalidNamesLocally(t *testing.T) {
	f := newFakeAPI(t)
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)
	for _, args := range [][]string{
		{"org", "create", strings.Repeat("a", 65)},
		{"org", "create", "  "},
		{"org", "create", "a\tb"},
		{"org", "update", testOrgA, "--name", "a\nb"},
		{"project", "create", strings.Repeat("a", 65)},
		{"project", "create", "default"},
		{"project", "update", "lab", "--name", "default"},
		{"project", "update", "lab", "--name", "\x7f"},
	} {
		_, errb, code := run(t, d, nil, args...)
		require.Equal(t, cerr.ExitUsage, code, "%q: %s", args, errb)
	}
	require.Empty(t, f.requests)
}

func TestOrgUpdate_lengthLimitExemptsTheCurrentName(t *testing.T) {
	// The contract's maxLength does not describe the unchanged-name exemption.
	t.Setenv("DAYTONA_TEST_SKIP_CONTRACT", "1")
	long := strings.Repeat("o", 70)
	f := newFakeAPI(t)
	f.on("GET", "/orgs/"+testOrgA, 200, organizationFixture(testOrgA, long, false))
	f.on("PATCH", "/orgs/"+testOrgA, 200, organizationFixture(testOrgA, long, false))
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)

	out, errb, code := run(t, d, nil, "org", "update", testOrgA, "--name", long, "-o", "json")
	require.Equal(t, 0, code, errb)
	require.Equal(t, long, mustJSON(t, out)["name"])
	require.Equal(t, "PATCH", f.requests[len(f.requests)-1].Method)

	f.requests = nil
	_, errb, code = run(t, d, nil, "org", "update", testOrgA, "--name", long+"x")
	require.Equal(t, cerr.ExitUsage, code, errb)
	require.Contains(t, errb, "at most 64 characters")
	for _, r := range f.requests {
		require.NotEqual(t, "PATCH", r.Method)
	}
}

func TestProjectUpdate_lengthLimitExemptsTheCurrentName(t *testing.T) {
	t.Setenv("DAYTONA_TEST_SKIP_CONTRACT", "1")
	long := strings.Repeat("p", 70)
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, map[string]any{"projects": []any{
		projectFixture(testProjP1, testOrgP, "default"),
		projectFixture(testProjP2, testOrgP, long),
	}})
	f.on("PATCH", "/projects/"+testProjP2, 200, projectFixture(testProjP2, testOrgP, long))
	d := cfgDir(t)
	oidcProfile(t, d, f.srv.URL, time.Hour)

	// By name: the resolved project carries its current name.
	_, errb, code := run(t, d, nil, "project", "update", long, "--name", long, "-o", "json")
	require.Equal(t, 0, code, errb)
	// By id: the current name is read from the project list.
	_, errb, code = run(t, d, nil, "project", "update", testProjP2, "--name", " "+long+" ", "-o", "json")
	require.Equal(t, 0, code, errb)

	f.requests = nil
	for _, ref := range []string{long, testProjP2} {
		_, errb, code = run(t, d, nil, "project", "update", ref, "--name", strings.Repeat("q", 65))
		require.Equal(t, cerr.ExitUsage, code, errb)
		require.Contains(t, errb, "at most 64 characters")
	}
	for _, r := range f.requests {
		require.NotEqual(t, "PATCH", r.Method)
	}
}
