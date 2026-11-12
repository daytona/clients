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
	"path/filepath"
	"testing"

	"github.com/daytona/clients/cli/internal/output"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSSH_printsHelp_whenNoCredential(t *testing.T) {
	// Given / When
	stdout, _, code := run(t, cfgDir(t), nil, "sandbox", "ssh", "--help")
	// Then
	require.Zero(t, code)
	require.Contains(t, stdout, "Usage:")
	require.Contains(t, stdout, "ssh")
}

func TestSSH_appliesCLIFlagsAndForwardsSSHFlags_whenSeparatedBySandboxID(t *testing.T) {
	// Given
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "Bearer explicit-key", r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/api-keys/current" {
			_, _ = io.WriteString(w, `{"id":"99999999-9999-9999-9999-999999999999","name":"fixture","created_at":"2026-09-13T00:00:00Z","project_id":"22222222-2222-2222-2222-222222222222","project_permissions":[],"sync_to_v0":false,"context":{"org_id":"11111111-1111-1111-1111-111111111111","org_name":"fixture-org","project_name":"fixture-project"}}`)
			return
		}
		assert.Equal(t, "/sandboxes/sbx", r.URL.Path)
		_, _ = io.WriteString(w, `{"id":"sbx","class":"linux","status":"running","toolbox_proxy_url":"https://toolbox.example","ssh_gateway_host":"ssh.example","ssh_gateway_port":2222}`)
	}))
	defer srv.Close()
	dir := cfgDir(t)
	require.NoError(t, os.WriteFile(filepath.Join(dir, "config.yaml"), []byte("active_profile: saved\nprofiles:\n  - name: selected\n    api_url: https://unused.example\n    credential: token\n"), 0o600))
	t.Setenv("DAYTONA_CONFIG_DIR", dir)
	root := NewRoot(&bytes.Buffer{}, &bytes.Buffer{})
	ssh, _, err := root.Find([]string{"sandbox", "ssh"})
	require.NoError(t, err)
	old := runSSHCommand
	t.Cleanup(func() { runSSHCommand = old })
	called := false
	runSSHCommand = func(_ context.Context, _ string, args []string, _ io.Writer) error {
		called = true
		a := FromContext(ssh.Context())
		require.True(t, a.Globals.Quiet)
		require.True(t, a.Globals.Debug)
		require.Equal(t, "selected", a.Globals.Profile)
		require.Equal(t, "11111111-1111-1111-1111-111111111111", a.Globals.Org)
		require.Equal(t, "22222222-2222-2222-2222-222222222222", a.Globals.Project)
		require.Equal(t, output.FormatJSON, a.Format)
		require.Equal(t, []string{"-o", "BatchMode=yes", "daytona-sandbox", "printf", "%s", "--help"}, args[2:])
		return nil
	}
	root.SetArgs([]string{"sandbox", "ssh", "-q", "--debug", "--profile", "selected", "--api-key", "explicit-key", "--api-url", srv.URL, "--org", "11111111-1111-1111-1111-111111111111", "--project", "22222222-2222-2222-2222-222222222222", "-o", "json", "sbx", "-o", "BatchMode=yes", "--", "printf", "%s", "--help"})
	// When
	err = root.ExecuteContext(t.Context())
	// Then
	require.NoError(t, err)
	require.True(t, called)
}
