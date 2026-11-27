// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"io"
	"net/http"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/cerr"
)

const settingsSandboxID = "11111111-1111-1111-1111-111111111111"

func TestSandboxAccessTokenSendsTheGrant(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "access-token", settingsSandboxID, "--operations", "ssh,toolbox:fs", "--ports", "none", "--ttl", "10m"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + settingsSandboxID, status: 200, response: sandboxDetailFixture(settingsSandboxID, "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/" + settingsSandboxID + "/access-token", status: 201, bodyJSON: `{"operations":["ssh","toolbox:fs"],"ports":[],"ttl_seconds":600}`, response: `{"access_token":"tok","token_type":"Bearer","expires_at":"2026-09-13T00:10:00Z","operations":["ssh","toolbox:fs"],"ports":[]}`},
	})
	require.NoError(t, err)
	out := requireJSONStdout(t, stdout)
	require.Equal(t, "tok", out["access_token"])
	require.Equal(t, []any{}, out["ports"])
}

func TestSandboxAccessTokenOmitsWhatIsNotGiven(t *testing.T) {
	_, _, err := runCLI(t, []string{"sandbox", "access-token", settingsSandboxID, "--ports", "3000,8080"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/" + settingsSandboxID, status: 200, response: sandboxDetailFixture(settingsSandboxID, "linux", "running")},
		{method: http.MethodPost, path: "/sandboxes/" + settingsSandboxID + "/access-token", status: 201, bodyJSON: `{"ports":[3000,8080]}`, response: `{"access_token":"tok","token_type":"Bearer","expires_at":"2026-09-13T00:10:00Z","operations":["toolbox"],"ports":[3000,8080]}`},
	})
	require.NoError(t, err)
}

func TestSandboxSettingsRefuseBadInputBeforeAnyRequest(t *testing.T) {
	for _, args := range [][]string{
		{"sandbox", "access-token", settingsSandboxID, "--ports", "2280"},
		{"sandbox", "access-token", settingsSandboxID, "--ports", "3000,,8080"},
		{"sandbox", "access-token", settingsSandboxID, "--operations", ""},
		{"sandbox", "access-token", settingsSandboxID, "--ttl", "1.5s"},
		{"sandbox", "secrets", "set", settingsSandboxID},
		{"sandbox", "secrets", "set", settingsSandboxID, "--secret", "A", "--clear"},
		{"sandbox", "secrets", "set", settingsSandboxID, "--secret", " "},
		{"sandbox", "secrets", "set", settingsSandboxID, "--clear", "--if-match", ""},
		{"sandbox", "network-policy", "set", settingsSandboxID},
		{"sandbox", "network-policy", "set", settingsSandboxID, "--allow-domain", "pypi.org", "--block-all=false"},
		{"sandbox", "network-policy", "set", settingsSandboxID, "--allow-cidr", "10.0.0.0/8"},
		{"sandbox", "port", "in-use", "sbx", "80"},
		{"sandbox", "port", "in-use", "sbx", "abc"},
	} {
		_, _, err := runCLI(t, args, nil)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v: %v", args, err)
	}
}

const sandboxSecretsBody = `{"secrets":["GITHUB_TOKEN"],"desired_revision":3,"applied_revision":2,"status":"pending"}`

func sandboxDetailHandler(w http.ResponseWriter, _ *http.Request) {
	_, _ = io.WriteString(w, sandboxDetailFixture(settingsSandboxID, "linux", "running"))
}

func TestSandboxSecretsGetPrintsTheETag(t *testing.T) {
	f := newFakeAPI(t)
	f.handle(http.MethodGet, "/sandboxes/"+settingsSandboxID, sandboxDetailHandler)
	f.handle(http.MethodGet, "/sandboxes/"+settingsSandboxID+"/secrets", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("ETag", `"rev-3"`)
		_, _ = io.WriteString(w, sandboxSecretsBody)
	})
	stdout, stderr, code := runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "-o", "json", "sandbox", "secrets", "get", settingsSandboxID)
	require.Equal(t, 0, code, stderr)
	out := mustJSON(t, stdout)
	require.Equal(t, `"rev-3"`, out["etag"])
	require.Equal(t, []any{"GITHUB_TOKEN"}, out["secrets"])
	require.Equal(t, float64(3), out["desired_revision"])
}

// Without --if-match, set reads the current ETag and sends it, so a change
// made in between is refused instead of overwritten; --if-match is sent as
// given with no read.
func TestSandboxSecretsSetIsConditional(t *testing.T) {
	for _, tc := range []struct {
		args      []string
		wantMatch string
		wantRead  bool
	}{
		{[]string{"--secret", "GITHUB_TOKEN", "--secret", "NPM_TOKEN"}, `"rev-3"`, true},
		{[]string{"--clear", "--if-match", `"rev-9"`}, `"rev-9"`, false},
	} {
		f := newFakeAPI(t)
		f.handle(http.MethodGet, "/sandboxes/"+settingsSandboxID, sandboxDetailHandler)
		read := false
		f.handle(http.MethodGet, "/sandboxes/"+settingsSandboxID+"/secrets", func(w http.ResponseWriter, _ *http.Request) {
			read = true
			w.Header().Set("ETag", `"rev-3"`)
			_, _ = io.WriteString(w, sandboxSecretsBody)
		})
		var ifMatch string
		f.handle(http.MethodPut, "/sandboxes/"+settingsSandboxID+"/secrets", func(w http.ResponseWriter, r *http.Request) {
			ifMatch = r.Header.Get("If-Match")
			w.Header().Set("ETag", `"rev-4"`)
			w.WriteHeader(http.StatusAccepted)
			_, _ = io.WriteString(w, `{"secrets":[],"desired_revision":4,"applied_revision":3,"status":"pending"}`)
		})
		args := append([]string{"--api-url", f.srv.URL, "--api-key", "k", "-o", "json", "sandbox", "secrets", "set", settingsSandboxID}, tc.args...)
		stdout, stderr, code := runReported(t, cfgDir(t), nil, args...)
		require.Equal(t, 0, code, stderr)
		require.Equal(t, tc.wantMatch, ifMatch)
		require.Equal(t, tc.wantRead, read)
		require.Equal(t, `"rev-4"`, mustJSON(t, stdout)["etag"])
		put := findRequest(t, f, http.MethodPut, "/sandboxes/"+settingsSandboxID+"/secrets")
		if tc.wantRead {
			require.Equal(t, []any{"GITHUB_TOKEN", "NPM_TOKEN"}, put.Body["secrets"])
		} else {
			require.Equal(t, []any{}, put.Body["secrets"])
		}
	}
}

const networkPolicyBody = `{"desired":{"allow_domains":["pypi.org"],"allow_cidrs":[],"block_all":true},"applied":null,"desired_revision":2,"applied_revision":1,"status":"pending"}`

func TestSandboxNetworkPolicySetAndReset(t *testing.T) {
	f := newFakeAPI(t)
	f.handle(http.MethodGet, "/sandboxes/"+settingsSandboxID, sandboxDetailHandler)
	f.handle(http.MethodGet, "/sandboxes/"+settingsSandboxID+"/network-policy", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("ETag", `"np-1"`)
		_, _ = io.WriteString(w, networkPolicyBody)
	})
	var bodies []string
	var matches []string
	f.handle(http.MethodPut, "/sandboxes/"+settingsSandboxID+"/network-policy", func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		bodies = append(bodies, string(b))
		matches = append(matches, r.Header.Get("If-Match"))
		w.Header().Set("ETag", `"np-2"`)
		w.WriteHeader(http.StatusAccepted)
		_, _ = io.WriteString(w, networkPolicyBody)
	})
	base := []string{"--api-url", f.srv.URL, "--api-key", "k", "-o", "json", "sandbox", "network-policy"}
	_, stderr, code := runReported(t, cfgDir(t), nil, append(base, "set", settingsSandboxID, "--allow-domain", "pypi.org", "--block-all", "--allow-cidr", "10.0.0.0/8")...)
	require.Equal(t, 0, code, stderr)
	_, stderr, code = runReported(t, cfgDir(t), nil, append(base, "reset", settingsSandboxID, "--if-match", `"np-7"`)...)
	require.Equal(t, 0, code, stderr)
	stdout, stderr, code := runReported(t, cfgDir(t), nil, append(base, "get", settingsSandboxID)...)
	require.Equal(t, 0, code, stderr)
	require.Equal(t, `"np-1"`, mustJSON(t, stdout)["etag"])

	require.Len(t, bodies, 2)
	assert.JSONEq(t, `{"allow_domains":["pypi.org"],"allow_cidrs":["10.0.0.0/8"],"block_all":true}`, bodies[0])
	assert.Equal(t, "null", bodies[1])
	assert.Equal(t, []string{`"np-1"`, `"np-7"`}, matches)
}

func TestSandboxPortInUse(t *testing.T) {
	stdout, _, err := runToolboxCLI(t, []string{"sandbox", "port", "in-use", "sbx", "3000"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodGet, path: "/test-tools/sbx/port/3000/in-use", status: 200, auth: true, response: `{"isInUse":true}`},
	})
	require.NoError(t, err)
	require.Equal(t, true, requireJSONStdout(t, stdout)["is_in_use"])
}
