// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"strings"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
)

// A credential no HTTP header can carry is refused before any request, as a
// usage error naming where it came from and what is wrong with it, never the
// value. Before, a control character failed inside the HTTP client and was
// reported as a network problem, and a non-ASCII one was sent as raw UTF-8.
func TestMalformedCredentialIsRefusedBeforeAnyRequest(t *testing.T) {
	const secret = "dtn_secretvalue"
	malformed := map[string]string{
		secret + "\n":       "a control character",
		secret + "\r":       "a control character",
		"dtn_\x00" + secret: "a control character",
		secret + "\tx":      "a control character",
		secret + "\x7f":     "a control character",
		secret + "é":        "a non-ASCII character",
	}
	for value, problem := range malformed {
		t.Run("flag", func(t *testing.T) {
			f := newFakeAPI(t)
			_, stderr, code := run(t, cfgDir(t), map[string]string{"DAYTONA_V2_API_URL": f.srv.URL}, "--api-key", value, "sandbox", "list")
			requireCredentialRefused(t, f, stderr, code, "--api-key contains "+problem, secret)
		})
		t.Run("env", func(t *testing.T) {
			if strings.ContainsRune(value, 0) {
				t.Skip("an environment variable cannot hold NUL")
			}
			f := newFakeAPI(t)
			_, stderr, code := run(t, cfgDir(t), map[string]string{"DAYTONA_V2_API_URL": f.srv.URL, "DAYTONA_API_KEY": value}, "sandbox", "list")
			requireCredentialRefused(t, f, stderr, code, "DAYTONA_API_KEY contains "+problem, secret)
		})
		t.Run("saved token", func(t *testing.T) {
			f := newFakeAPI(t)
			d := cfgDir(t)
			st, err := config.Open(d)
			require.NoError(t, err)
			require.NoError(t, st.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
				require.NoError(t, c.Upsert(config.Profile{Name: "default", APIURL: f.srv.URL, Credential: "token"}))
				c.ActiveProfile = "default"
				cr.Profiles["default"] = config.ProfileCredentials{Token: value}
				return true, true, nil
			}))
			_, stderr, code := run(t, d, nil, "sandbox", "list")
			requireCredentialRefused(t, f, stderr, code, `profile "default"'s saved credential contains `+problem, secret)
		})
		t.Run("login --token", func(t *testing.T) {
			f := newFakeAPI(t)
			d := cfgDir(t)
			_, stderr, code := run(t, d, map[string]string{"DAYTONA_V2_API_URL": f.srv.URL}, "login", "--token", value)
			requireCredentialRefused(t, f, stderr, code, "--token contains "+problem, secret)
			st, err := config.Open(d)
			require.NoError(t, err)
			creds, err := st.LoadCredentials()
			require.NoError(t, err)
			require.Empty(t, creds.Profiles, "nothing is saved")
		})
	}
}

func requireCredentialRefused(t *testing.T, f *fakeAPI, stderr string, code int, message, secret string) {
	t.Helper()
	require.Equal(t, cerr.ExitUsage, code, stderr)
	require.Contains(t, stderr, message)
	require.NotContains(t, stderr, secret)
	require.NotContains(t, stderr, "network")
	require.Empty(t, f.requests)
}

// A space inside a credential is still sent as given: only what a header
// cannot carry is refused.
func TestCredentialWithASpaceIsStillSent(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/sandboxes", 200, map[string]any{"sandboxes": []any{}})
	_, stderr, code := run(t, cfgDir(t), map[string]string{"DAYTONA_V2_API_URL": f.srv.URL}, "--api-key", "a b", "sandbox", "list", "--region", "region-b")
	require.NotEqual(t, cerr.ExitUsage, code, stderr)
	require.NotEmpty(t, f.requests)
	require.Equal(t, "Bearer a b", f.requests[0].Auth)
}

// With no credential at all, a command that needs an organization or a
// project says "not logged in" (exit 3) before asking for the missing
// selection (exit 2), and sends nothing.
func TestNoCredentialIsReportedBeforeAMissingOrgOrProject(t *testing.T) {
	for _, args := range [][]string{
		{"key", "list"},
		{"invite", "list"},
		{"project", "use", "foo"},
		{"project", "list"},
		{"member", "list"},
		{"audit", "list"},
		{"service-account", "list"},
		{"org-key", "list"},
	} {
		f := newFakeAPI(t)
		_, stderr, code := run(t, cfgDir(t), map[string]string{"DAYTONA_V2_API_URL": f.srv.URL}, args...)
		require.Equal(t, cerr.ExitAuth, code, "%v: %s", args, stderr)
		require.Contains(t, stderr, "not logged in", args)
		require.Empty(t, f.requests, args)
	}
}

// With a credential, a missing organization is still the usage error.
func TestMissingOrgWithACredentialIsStillAUsageError(t *testing.T) {
	f := newFakeAPI(t)
	_, stderr, code := run(t, cfgDir(t), map[string]string{"DAYTONA_V2_API_URL": f.srv.URL, "DAYTONA_API_KEY": "k"}, "audit", "list")
	require.Equal(t, cerr.ExitUsage, code, stderr)
	require.Contains(t, stderr, "an organization is required")
}
