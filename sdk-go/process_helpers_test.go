// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func processTestSandbox(t *testing.T, routes map[string]http.HandlerFunc) (*Sandbox, *fakeServer) {
	t.Helper()
	fake := newFakeServer(t, routes)
	client := newTestClient(t, fake.Server)
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetOptions{ToolboxURL: fake.Server.URL, AccessToken: strings.Join([]string{"test", "sandbox"}, "-")},
	})
	require.NoError(t, err)
	return sandbox, fake
}

func processFixture(id string) map[string]any {
	return map[string]any{
		"id": id, "kind": "exec", "state": "running", "cwd": "/",
		"createdAt": "2026-01-01T00:00:00Z", "stdin": "closed", "keepLogs": "on_exit_ttl", "truncatedHead": false,
	}
}

func processFrameFixture(channel, data string) map[string]any {
	return map[string]any{"cursor": "1", "channel": channel, "data": data, "timestamp": 1}
}

func processLogsFixture(frames ...map[string]any) map[string]any {
	if frames == nil {
		frames = []map[string]any{}
	}
	return map[string]any{"frames": frames, "nextCursor": "1", "eof": true, "truncatedHead": false}
}
