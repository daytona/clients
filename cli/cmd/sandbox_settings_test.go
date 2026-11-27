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
		{"sandbox", "port", "in-use", "sbx", "0"},
		{"sandbox", "port", "in-use", "sbx", "65536"},
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

const networkPolicyBody = `{"desired":{"version":1,"allow_domains":["pypi.org"],"allow_cidrs":[],"block_all":true},"applied":null,"desired_revision":2,"applied_revision":1,"status":"pending"}`

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

// A port outside the sandbox's current range is left for the sandbox to
// refuse, as in the SDKs.
func TestSandboxPortInUseLeavesTheRangeToTheSandbox(t *testing.T) {
	_, _, err := runToolboxCLI(t, []string{"sandbox", "port", "in-use", "sbx", "80"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodGet, path: "/test-tools/sbx/port/80/in-use", status: 400, auth: true, response: `{"code":"BAD_REQUEST","message":"port must be from 3000 to 9999","source":"TOOLBOX"}`},
	})
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v", err)
}

// A mint response without a usable token is an error, never printed as a
// success; the message never carries the token.
func TestSandboxAccessTokenRefusesAMalformedResponse(t *testing.T) {
	for name, response := range map[string]string{
		"empty token":     `{"access_token":"","token_type":"Bearer","expires_at":"2026-09-13T00:10:00Z","operations":[]}`,
		"no expiry":       `{"access_token":"tok-secret","token_type":"Bearer","operations":[]}`,
		"no operations":   `{"access_token":"tok-secret","token_type":"Bearer","expires_at":"2026-09-13T00:10:00Z"}`,
		"negative port":   `{"access_token":"tok-secret","token_type":"Bearer","expires_at":"2026-09-13T00:10:00Z","operations":[],"ports":[-1]}`,
		"no token type":   `{"access_token":"tok-secret","expires_at":"2026-09-13T00:10:00Z","operations":[]}`,
		"null token type": `{"access_token":"tok-secret","token_type":null,"expires_at":"2026-09-13T00:10:00Z","operations":[]}`,
	} {
		stdout, _, err := runCLI(t, []string{"sandbox", "access-token", settingsSandboxID}, []expectedRequest{
			{method: http.MethodGet, path: "/sandboxes/" + settingsSandboxID, status: 200, response: sandboxDetailFixture(settingsSandboxID, "linux", "running")},
			{method: http.MethodPost, path: "/sandboxes/" + settingsSandboxID + "/access-token", status: 201, bodyJSON: `{}`, response: response, skipResponseContract: true},
		})
		require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err), "%s: %v", name, err)
		require.NotContains(t, err.Error(), "tok-secret", name)
		require.Empty(t, stdout, name)
	}
}

// Secret names are sent trimmed.
func TestSandboxSecretsSetTrimsNames(t *testing.T) {
	f := newFakeAPI(t)
	f.handle(http.MethodGet, "/sandboxes/"+settingsSandboxID, sandboxDetailHandler)
	f.handle(http.MethodPut, "/sandboxes/"+settingsSandboxID+"/secrets", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("ETag", `"rev-4"`)
		w.WriteHeader(http.StatusAccepted)
		_, _ = io.WriteString(w, sandboxSecretsBody)
	})
	_, stderr, code := runReported(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "k", "-o", "json", "sandbox", "secrets", "set", settingsSandboxID, "--secret", " GITHUB_TOKEN ", "--if-match", `"rev-3"`)
	require.Equal(t, 0, code, stderr)
	require.Equal(t, []any{"GITHUB_TOKEN"}, findRequest(t, f, http.MethodPut, "/sandboxes/"+settingsSandboxID+"/secrets").Body["secrets"])
}

// A settings response whose body is JSON null, or that carries no ETag (the
// token the next update needs), is an error rather than a panic or an empty
// etag.
func TestSandboxSettingsRefuseANullBodyOrAMissingETag(t *testing.T) {
	for _, tc := range []struct {
		name   string
		args   []string
		method string
		etag   string
		body   string
	}{
		{"get null body", []string{"secrets", "get", settingsSandboxID}, http.MethodGet, `"rev-3"`, `null`},
		{"get no etag", []string{"network-policy", "get", settingsSandboxID}, http.MethodGet, "", networkPolicyBody},
		{"put null body", []string{"secrets", "set", settingsSandboxID, "--clear", "--if-match", `"rev-3"`}, http.MethodPut, `"rev-4"`, `null`},
		{"put no etag", []string{"network-policy", "reset", settingsSandboxID, "--if-match", `"np-1"`}, http.MethodPut, "", networkPolicyBody},
	} {
		f := newFakeAPI(t)
		f.handle(http.MethodGet, "/sandboxes/"+settingsSandboxID, sandboxDetailHandler)
		route := "/sandboxes/" + settingsSandboxID + "/" + tc.args[0]
		f.handle(tc.method, route, func(w http.ResponseWriter, _ *http.Request) {
			if tc.etag != "" {
				w.Header().Set("ETag", tc.etag)
			}
			if tc.body == "null" {
				// Off-contract on purpose: a non-JSON media type skips the
				// harness's fixture check; the CLI decodes the body anyway.
				w.Header().Set("Content-Type", "text/plain")
			}
			if tc.method == http.MethodPut {
				w.WriteHeader(http.StatusAccepted)
			}
			_, _ = io.WriteString(w, tc.body)
		})
		args := append([]string{"--api-url", f.srv.URL, "--api-key", "k", "-o", "json", "sandbox"}, tc.args...)
		stdout, stderr, code := runReported(t, cfgDir(t), nil, args...)
		require.Equal(t, cerr.ExitFailure, code, "%s: %s", tc.name, stderr)
		require.Empty(t, stdout, tc.name)
	}
}
