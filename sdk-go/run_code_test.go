// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func interpreterRunRoutes(t *testing.T) map[string]http.HandlerFunc {
	t.Helper()
	return map[string]http.HandlerFunc{
		"POST /processes":       jsonHandler(t, interpreterProcessFixture("p")),
		"GET /processes/p/wait": jsonHandler(t, map[string]any{"reason": "exited", "exitCode": 0}),
		"GET /processes/p/logs": jsonHandler(t, interpreterEmptyLogs()),
	}
}

func TestRunCode_whenImplicit(t *testing.T) {
	// Given
	fake := newFakeServer(t, interpreterRunRoutes(t))
	sandbox := interpreterTestSandbox(t, fake)
	// When
	result, err := sandbox.RunCode(t.Context(), "", &RunCodeOptions{
		Cwd: "/work", Env: map[string]string{"A": "B"}, Timeout: WaitForever,
	})
	// Then
	require.NoError(t, err)
	require.Equal(t, "p", result.Process.ProcessID)
	require.Equal(t, ProcessReasonExited, result.Process.Reason)
	require.NotNil(t, result.Process.ExitCode)
	require.Zero(t, *result.Process.ExitCode)
	require.JSONEq(t,
		`{"code":"","kind":"code","language":"python","cwd":"/work","env":{"A":"B"},"keepLogs":"on_exit_ttl"}`,
		string(fake.requests()[0].Body))
}

func TestRunCode_whenExplicitContextLost(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /processes": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusNotFound)
			writeJSON(t, w, map[string]any{"code": "SESSION_NOT_FOUND", "source": "DAYTONA_DAEMON", "message": "lost"})
		},
	})
	sandbox := interpreterTestSandbox(t, fake)
	bound, err := sandbox.Interpreter().ConnectContext("ctx", "python")
	require.NoError(t, err)
	// When
	_, err = bound.Run(t.Context(), "x = 1", nil)
	// Then
	require.ErrorIs(t, err, ErrSessionNotFound)
	require.Len(t, fake.requests(), 1)
	require.JSONEq(t,
		`{"code":"x = 1","kind":"code","language":"python","sessionId":"ctx","keepLogs":"on_exit_ttl"}`,
		string(fake.requests()[0].Body))
}

func TestRunCode_whenInvalidOptions(t *testing.T) {
	tests := []struct {
		name string
		opts RunCodeOptions
	}{
		{"bare context requires language", RunCodeOptions{ContextID: "ctx"}},
		{"isolated explicit", RunCodeOptions{ContextID: "ctx", Language: "python", Isolated: true}},
		{"explicit env", RunCodeOptions{ContextID: "ctx", Language: "python", Env: map[string]string{}}},
		{"invalid id", RunCodeOptions{ContextID: "..", Language: "python"}},
		{"negative duration", RunCodeOptions{Timeout: -time.Second}},
		{"no wait", RunCodeOptions{Timeout: NoWait}},
		{"zero handle", RunCodeOptions{Context: &InterpreterContext{}}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := interpreterTestSandbox(t, fake)
			// When
			_, err := sandbox.RunCode(t.Context(), "1", &test.opts)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestRunCode_whenContextLanguageConflicts(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	sandbox := interpreterTestSandbox(t, fake)
	bound, err := sandbox.Interpreter().ConnectContext("ctx", "python")
	require.NoError(t, err)
	// When
	_, err = sandbox.RunCode(t.Context(), "1", &RunCodeOptions{Context: bound, Language: "javascript"})
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}

func TestRunCode_whenIsolatedUsesServerIdentity(t *testing.T) {
	// Given
	routes := interpreterRunRoutes(t)
	routes["POST /sessions"] = jsonHandler(t, map[string]any{"id": "chosen", "kernel": "python"})
	routes["DELETE /sessions/chosen"] = func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}
	fake := newFakeServer(t, routes)
	sandbox := interpreterTestSandbox(t, fake)
	// When
	result, err := sandbox.RunCode(t.Context(), "1", &RunCodeOptions{Isolated: true})
	// Then
	require.NoError(t, err)
	require.Equal(t, "p", result.Process.ProcessID)
	requests := fake.requests()
	require.Len(t, requests, 5)
	var body map[string]json.RawMessage
	require.NoError(t, json.Unmarshal(requests[1].Body, &body))
	require.JSONEq(t, `"chosen"`, string(body["sessionId"]))
	require.Equal(t, "/sessions/chosen", requests[4].Path)
	require.Empty(t, sandbox.Interpreter().queues)
}

func TestRunCode_whenIsolatedSetupResponseMalformed(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sessions":        jsonHandler(t, map[string]any{"kernel": "python"}),
		"DELETE /sessions/{id}": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
	})
	sandbox := interpreterTestSandbox(t, fake)
	// When
	_, err := sandbox.RunCode(t.Context(), "1", &RunCodeOptions{Isolated: true})
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	requests := fake.requests()
	require.Len(t, requests, 2)
	var created interpreterContextRequest
	require.NoError(t, json.Unmarshal(requests[0].Body, &created))
	require.Equal(t, "/sessions/"+created.ID, requests[1].Path)
}
