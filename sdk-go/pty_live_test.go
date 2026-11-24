// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package daytona

import (
	"context"
	"crypto/rand"
	"net/http"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func newPTYLiveSandbox(t *testing.T) *Sandbox {
	t.Helper()
	if os.Getenv("DAYTONA_API_KEY") == "" {
		t.Skip("DAYTONA_API_KEY is not set")
	}
	client, err := New()
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, client.Close()) })
	name := "gosdk-pty-" + strings.ToLower(rand.Text()[:10])
	body := struct {
		Name        string            `json:"name"`
		Labels      map[string]string `json:"labels"`
		WaitSeconds int               `json:"wait_seconds"`
	}{name, map[string]string{"purpose": "gosdk-e2e", "name": name}, 60}
	var created struct {
		ID string `json:"id"`
	}
	require.NoError(t, client.api().do(t.Context(), apiRequest{
		Method: http.MethodPost, Path: "/sandboxes", Body: body, Result: &created, Timeout: 65 * time.Second,
	}))
	segment, err := pathSegment(created.ID)
	require.NoError(t, err)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.WithoutCancel(t.Context()), time.Minute)
		defer cancel()
		require.NoError(t, client.api().do(ctx, apiRequest{
			Method: http.MethodDelete, Path: "/sandboxes/" + segment,
		}))
	})
	require.NoError(t, client.api().do(t.Context(), apiRequest{
		Path: "/sandboxes/" + segment, Query: url.Values{"wait": {"running"}, "timeout_ms": {"60000"}},
		Timeout: 65 * time.Second,
	}))
	sandbox, err := client.Get(t.Context(), created.ID, nil)
	require.NoError(t, err)
	require.Equal(t, SandboxStateStarted, sandbox.State())
	return sandbox
}

func TestPTYLive_whenCreatingAndReconnecting(t *testing.T) {
	// Given
	sandbox := newPTYLiveSandbox(t)
	ctx, cancel := context.WithTimeout(t.Context(), time.Minute)
	defer cancel()
	connection, err := sandbox.PTY().Create(ctx, "", &PTYOptions{Tag: "pty-live"})
	require.NoError(t, err)
	defer func() { require.NoError(t, connection.Close()) }()
	id := connection.Handle().ID()
	// When: send a command whose literal input does not contain its expected output.
	_, err = connection.Write([]byte("printf '\\160\\164\\171\\055\\157\\153\\012'\n"))
	require.NoError(t, err)
	var output strings.Builder
	buffer := make([]byte, 4096)
	for !strings.Contains(output.String(), "pty-ok") {
		n, readErr := connection.Read(buffer)
		require.NoError(t, readErr)
		output.Write(buffer[:n])
	}
	// Then
	require.Contains(t, output.String(), "pty-ok")
	require.NoError(t, connection.Resize(ctx, TerminalSize{Cols: 110, Rows: 35}))
	page, err := sandbox.PTY().List(ctx)
	require.NoError(t, err)
	found := false
	for _, item := range page.Items {
		if item.ID == id {
			found = true
			require.Equal(t, ProcessKindPTY, item.Kind)
		}
	}
	require.True(t, found)
	require.NoError(t, connection.Close())
	reconnected, err := sandbox.PTY().Connect(ctx, id)
	require.NoError(t, err)
	require.Equal(t, id, reconnected.Handle().ID())
	require.NoError(t, reconnected.Close())
	require.NoError(t, sandbox.PTY().Delete(ctx, id))
}
