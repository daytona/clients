// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func newPTYTestSandbox(t *testing.T, server *httptest.Server, opts ...Option) *Sandbox {
	t.Helper()
	client := newTestClient(t, server, opts...)
	sandbox, err := client.newSandbox(sandboxInit{
		Info: SandboxInfo{ID: "sbx"}, ToolboxURL: server.URL + "/toolbox",
		Options: GetSandboxOptions{AccessToken: "terminal-caller"},
	})
	require.NoError(t, err)
	return sandbox
}

func ptyFixture(kind string) map[string]any {
	return map[string]any{
		"id": "p", "kind": kind, "state": "running", "cwd": "/home/daytona",
		"stdin": "open", "keepLogs": "until_cleanup", "truncatedHead": false,
		"createdAt": "2026-01-02T03:04:05Z",
	}
}

func TestPTYStart_whenDefaults(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /toolbox/sbx/processes": jsonHandler(t, ptyFixture("pty")),
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	// When
	handle, err := sandbox.PTY().Start(t.Context(), "", nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, "p", handle.ID())
	require.Equal(t, ProcessKindPTY, handle.Kind())
	require.JSONEq(t, `{
		"kind":"pty","env":{"TERM":"xterm-256color"},"stdin":"open",
		"keepLogs":"until_cleanup","terminal":{"cols":80,"rows":24}
	}`, string(fake.requests()[0].Body))
}

func TestPTYStart_whenOptionsOverrideDefaults(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /toolbox/sbx/processes": jsonHandler(t, ptyFixture("pty")),
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	login := false
	env := map[string]string{"TERM": "", "EXAMPLE": "value"}
	opts := &PTYOptions{
		Cwd: "/work", Env: env, Name: "shell", Tag: "task-1", User: "worker",
		Shell: "/bin/sh", Login: &login, Stdin: ProcessStdinClosed,
		ExecutionTimeout: time.Nanosecond, KeepLogs: ProcessKeepLogsNone,
		Terminal: &TerminalSize{Cols: 120, Rows: 40},
	}
	// When
	_, err := sandbox.PTY().Start(t.Context(), "printf hi", opts)
	// Then
	require.NoError(t, err)
	require.JSONEq(t, `{
		"kind":"pty","shellCommand":"printf hi","cwd":"/work","env":{"TERM":"","EXAMPLE":"value"},
		"name":"shell","tag":"task-1","user":"worker","shell":"/bin/sh","login":false,
		"stdin":"closed","timeoutMs":1,"keepLogs":"none","terminal":{"cols":120,"rows":40}
	}`, string(fake.requests()[0].Body))
	require.Empty(t, env["TERM"])
}

func TestPTYStart_whenAddingTERMDoesNotMutateOptions(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /toolbox/sbx/processes": jsonHandler(t, ptyFixture("pty")),
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	env := map[string]string{"EXAMPLE": "value"}
	// When
	_, err := sandbox.PTY().Start(t.Context(), "", &PTYOptions{Env: env})
	// Then
	require.NoError(t, err)
	require.Equal(t, map[string]string{"EXAMPLE": "value"}, env)
	var body struct {
		Env map[string]string `json:"env"`
	}
	require.NoError(t, json.Unmarshal(fake.requests()[0].Body, &body))
	require.Equal(t, map[string]string{"EXAMPLE": "value", "TERM": "xterm-256color"}, body.Env)
}

func TestPTYStart_whenInvalidOptions(t *testing.T) {
	for _, test := range []struct {
		name string
		opts PTYOptions
	}{
		{"zero cols", PTYOptions{Terminal: &TerminalSize{Cols: 0, Rows: 24}}},
		{"negative rows", PTYOptions{Terminal: &TerminalSize{Cols: 80, Rows: -1}}},
		{"cols overflow", PTYOptions{Terminal: &TerminalSize{Cols: 65536, Rows: 24}}},
		{"rows overflow", PTYOptions{Terminal: &TerminalSize{Cols: 80, Rows: 65536}}},
		{"tag dot", PTYOptions{Tag: "."}},
		{"tag traversal", PTYOptions{Tag: ".."}},
		{"tag unsafe", PTYOptions{Tag: "space tag"}},
		{"tag too long", PTYOptions{Tag: strings.Repeat("x", 129)}},
		{"stdin", PTYOptions{Stdin: ProcessStdinUnknown}},
		{"retention", PTYOptions{KeepLogs: ProcessKeepLogsUnknown}},
		{"timeout", PTYOptions{ExecutionTimeout: -time.Second}},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := newPTYTestSandbox(t, fake.Server)
			// When
			handle, err := sandbox.PTY().Start(t.Context(), "", &test.opts)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Nil(t, handle)
			require.Empty(t, fake.requests())
		})
	}
}

func TestPTYList_whenFilteringTerminals(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/processes": jsonHandler(t, map[string]any{"items": []any{ptyFixture("pty")}}),
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	// When
	page, err := sandbox.PTY().List(t.Context())
	// Then
	require.NoError(t, err)
	require.Len(t, page.Items, 1)
	require.Equal(t, "p", page.Items[0].ID)
	require.Equal(t, ProcessKindPTY, page.Items[0].Kind)
	require.Equal(t, "pty", fake.requests()[0].Query.Get("kind"))
	require.Len(t, fake.requests(), 1)
}
