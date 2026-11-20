// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestToolboxSSH_neverExecutesSSH_whenTokenMintFails(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status int
		body   string
		exit   int
	}{
		{"forbidden", http.StatusForbidden, `{"code":"FORBIDDEN","message":"not permitted"}`, cerr.ExitForbidden},
		{"unavailable", http.StatusServiceUnavailable, `{"code":"SERVICE_UNAVAILABLE","message":"temporarily unavailable"}`, cerr.ExitFailure},
		{"empty token", http.StatusCreated, `{"access_token":""}`, cerr.ExitFailure},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given a resolved gateway whose token mint cannot supply a token.
			t.Setenv("DAYTONA_CONFIG_DIR", cfgDir(t))
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				switch r.URL.Path {
				case "/sandboxes/sbx":
					_, _ = io.WriteString(w, `{"id":"sbx","class":"linux","status":"running","toolbox_proxy_url":"https://tb.example","ssh_gateway_host":"ssh.example","ssh_gateway_port":2222}`)
				case "/sandboxes/sbx/access-token":
					w.Header().Set("Retry-After", "1")
					w.WriteHeader(tc.status)
					_, _ = io.WriteString(w, tc.body)
				default:
					t.Errorf("unexpected request: %s %s", r.Method, r.URL)
					http.NotFound(w, r)
				}
			}))
			defer server.Close()
			a, err := app.Load(app.Globals{APIURL: server.URL, APIKey: "project-key-fixture", APIKeySet: true, Output: "json"}, &bytes.Buffer{}, &bytes.Buffer{}, nil)
			require.NoError(t, err)
			cmd := newSandboxSSHSubcommand()
			cmd.SetContext(context.WithValue(t.Context(), ctxKey{}, a))
			old := runSSHCommand
			t.Cleanup(func() { runSSHCommand = old })
			called := false
			runSSHCommand = func(context.Context, string, []string, io.Writer) error {
				called = true
				return nil
			}

			// When connecting.
			err = cmd.RunE(cmd, []string{"sbx"})

			// Then the mapped error is returned without executing SSH.
			require.Error(t, err)
			require.False(t, called)
			require.Equal(t, tc.exit, cerr.ExitCode(err))
			switch tc.status {
			case http.StatusCreated:
				require.Contains(t, err.Error(), "access_token")
			default:
				var ae *api.APIError
				require.ErrorAs(t, err, &ae)
				require.Equal(t, tc.status, ae.Status)
			}
			if tc.status == http.StatusServiceUnavailable {
				require.Contains(t, cerr.DetailsOf(err).Hint, "retry")
			}
		})
	}
}

func TestToolboxSSH_usesPlatformToken_whenBrowserLoginIsAvailable(t *testing.T) {
	// Given a browser login with a platform token and no project key.
	t.Setenv("DAYTONA_CONFIG_DIR", cfgDir(t))
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "Bearer platform-token", r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/sandboxes/sbx":
			_, _ = io.WriteString(w, `{"id":"sbx","class":"linux","status":"running","toolbox_proxy_url":"https://tb.example","ssh_gateway_host":"ssh.example","ssh_gateway_port":2222}`)
		case "/sandboxes/sbx/access-token":
			w.WriteHeader(http.StatusCreated)
			_, _ = io.WriteString(w, `{"access_token":"sandbox-ssh-token"}`)
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL)
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	const projectID = "22222222-2222-2222-2222-222222222222"
	a := &app.App{
		Profile:          &config.Profile{APIURL: server.URL, OrgID: "11111111-1111-1111-1111-111111111111", ProjectByOrg: map[string]string{"11111111-1111-1111-1111-111111111111": projectID}},
		Format:           output.FormatJSON,
		CredentialSource: "profile-oidc",
		PlatformTokenFn: func(_ context.Context, project, rejected string) (string, bool, error) {
			require.Equal(t, projectID, project)
			require.Empty(t, rejected)
			return "platform-token", true, nil
		},
		WorkloadCredentialFn: func(context.Context, string, string) (string, error) {
			t.Error("SSH must not obtain a project key when a platform token is available")
			return "", cerr.New(cerr.ExitFailure, "unexpected project key request")
		},
	}
	cmd := newSandboxSSHSubcommand()
	cmd.SetContext(context.WithValue(t.Context(), ctxKey{}, a))
	old := runSSHCommand
	t.Cleanup(func() { runSSHCommand = old })
	called := false
	runSSHCommand = func(_ context.Context, _ string, args []string, _ io.Writer) error {
		called = true
		cfg, err := os.ReadFile(args[1])
		require.NoError(t, err)
		require.Contains(t, string(cfg), `User "sbx#sandbox-ssh-token"`)
		return nil
	}

	// When connecting with the browser login.
	err := cmd.RunE(cmd, []string{"sbx"})

	// Then only the sandbox token is used for SSH.
	require.NoError(t, err)
	require.True(t, called)
}
