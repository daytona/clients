// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestToolboxSSH_buildsSSHInvocation(t *testing.T) {
	t.Setenv("DAYTONA_CONFIG_DIR", cfgDir(t))
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/sandboxes/sbx", r.URL.Path)
		assert.Equal(t, "Bearer k", r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"sbx","class":"linux","spot":false,"name":"box","status":"running","region_id":"region-b","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"port_map":[],"disk_mib":1024,"memory_mib":512,"cpu":1,"cores_max":1,"toolbox_proxy_url":"https://tb.example","ssh_gateway_host":"ssh.example","ssh_gateway_port":2222}`))
	}))
	defer server.Close()

	a, err := app.Load(app.Globals{APIURL: server.URL, APIKey: "k", APIKeySet: true, Output: "json"}, &bytes.Buffer{}, &bytes.Buffer{}, nil)
	require.NoError(t, err)
	a.SetCredentialResolver("flag", func(context.Context) (string, error) { return "k", nil })
	cmd := newSandboxSSHSubcommand()
	cmd.SetContext(context.WithValue(context.Background(), ctxKey{}, a))

	oldSSHCommand := runSSHCommand
	runSSHCommand = func(_ context.Context, binary string, args []string) error {
		require.Contains(t, binary, "ssh")
		// The credential-bearing username lives in a 0600 ssh_config passed
		// with -F, never in argv (visible to every local user via ps).
		require.Len(t, args, 7)
		require.Equal(t, "-F", args[0])
		require.Equal(t, []string{"-L", "8080:localhost:80", "daytona-sandbox", "uname", "-a"}, args[2:])
		for _, a := range args {
			require.NotContains(t, a, "sbx#k")
		}
		cfg, err := os.ReadFile(args[1])
		require.NoError(t, err)
		require.Contains(t, string(cfg), "HostName ssh.example")
		require.Contains(t, string(cfg), "Port 2222")
		require.Contains(t, string(cfg), `User "sbx#k"`)
		return nil
	}
	t.Cleanup(func() { runSSHCommand = oldSSHCommand })

	require.NoError(t, cmd.RunE(cmd, []string{"sbx", "-L", "8080:localhost:80", "--", "uname", "-a"}))
}
