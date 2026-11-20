// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func interpreterTestSandbox(t *testing.T, fake *fakeServer) *Sandbox {
	t.Helper()
	client := newTestClient(t, fake.Server)
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetSandboxOptions{ToolboxURL: fake.Server.URL, AccessToken: "sandbox-fixture"},
	})
	require.NoError(t, err)
	return sandbox
}

func interpreterProcessFixture(id string) map[string]any {
	return map[string]any{
		"id": id, "kind": "code", "state": "running", "cwd": "/", "stdin": "closed",
		"keepLogs": "on_exit_ttl", "createdAt": "2026-01-01T00:00:00Z", "truncatedHead": false,
	}
}

func interpreterEmptyLogs() map[string]any {
	return map[string]any{"frames": []any{}, "nextCursor": "0", "truncatedHead": false, "eof": true}
}

func TestInterpreterCreateContext_whenValid(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sessions": jsonHandler(t, map[string]any{"id": "chosen", "kernel": "python"}),
	})
	service := interpreterTestSandbox(t, fake).Interpreter()
	// When
	bound, err := service.CreateContext(t.Context(), "python", &CreateInterpreterContextOptions{
		ContextID: "requested", Cwd: "/workspace", Env: map[string]string{"A": "B"},
	})
	// Then
	require.NoError(t, err)
	require.Equal(t, "chosen", bound.ID())
	require.Equal(t, "python", bound.Language())
	require.Equal(t, "sbx", bound.SandboxID())
	requests := fake.requests()
	require.Len(t, requests, 1)
	require.JSONEq(t, `{"id":"requested","kernel":"python","cwd":"/workspace","env":{"A":"B"}}`,
		string(requests[0].Body))
}

func TestInterpreterCreateContext_whenResponseMalformed_returnsTopLevelSDKError(t *testing.T) {
	// Given an accepted named context whose response omits the kernel.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sessions": jsonHandler(t, map[string]string{"id": "requested"}),
	})
	service := interpreterTestSandbox(t, fake).Interpreter()
	// When creation cannot parse the accepted response.
	_, err := service.CreateContext(t.Context(), "python", &CreateInterpreterContextOptions{ContextID: "requested"})
	// Then errors.As returns the top-level SDK error, retaining the uncertain ID and cause.
	var sdkError *Error
	require.ErrorAs(t, err, &sdkError)
	require.Same(t, sdkError, err)
	require.Contains(t, sdkError.Message, `context "requested" may have been created`)
	require.ErrorIs(t, sdkError.Err, ErrBadRequest)
	require.ErrorIs(t, err, ErrBadRequest)
}

func TestInterpreterListContexts_whenShellAndUnknownLanguages(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sessions": jsonHandler(t, map[string]any{"items": []any{
			map[string]any{"id": "shell", "kernel": "shell"},
			map[string]any{"id": "future", "kernel": "future-language", "createdAt": "ignored"},
		}}),
	})
	service := interpreterTestSandbox(t, fake).Interpreter()
	// When
	contexts, err := service.ListContexts(t.Context())
	// Then
	require.NoError(t, err)
	require.Len(t, contexts, 1)
	require.Equal(t, &InterpreterContextJSON{
		SandboxID: "sbx", ContextID: "future", Language: "future-language",
	}, contexts[0].Identity())
}

func TestInterpreterContextIdentity_whenReconnected(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	service := interpreterTestSandbox(t, fake).Interpreter()
	identity := InterpreterContextJSON{SandboxID: "sbx", ContextID: "a/b", Language: "python"}
	// When
	bound, err := service.ConnectContextJSON(identity)
	// Then
	require.NoError(t, err)
	encoded, err := json.Marshal(bound)
	require.NoError(t, err)
	require.JSONEq(t, `{"sandboxId":"sbx","contextId":"a/b","language":"python"}`, string(encoded))
	require.Empty(t, fake.requests())
}

func TestInterpreterDeleteContext_whenIDNeedsEscaping(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"DELETE /sessions/{id}": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
	})
	service := interpreterTestSandbox(t, fake).Interpreter()
	bound, err := service.ConnectContext("a/b", "python")
	require.NoError(t, err)
	// When
	err = bound.Close(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, "/sessions/a%2Fb", fake.requests()[0].Path)
}

func TestInterpreterConnect_whenInvalid(t *testing.T) {
	tests := []InterpreterContextJSON{
		{SandboxID: "foreign", ContextID: "c", Language: "python"},
		{SandboxID: "sbx", ContextID: "", Language: "python"},
		{SandboxID: "sbx", ContextID: " ", Language: "python"},
		{SandboxID: "sbx", ContextID: ".", Language: "python"},
		{SandboxID: "sbx", ContextID: "..", Language: "python"},
		{SandboxID: "sbx", ContextID: "\xff", Language: "python"},
		{SandboxID: "sbx", ContextID: "c", Language: ""},
	}
	for _, identity := range tests {
		t.Run(identity.SandboxID+"/"+identity.ContextID+"/"+identity.Language, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			service := interpreterTestSandbox(t, fake).Interpreter()
			// When
			_, err := service.ConnectContextJSON(identity)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestInterpreterListContexts_whenMalformed(t *testing.T) {
	for _, raw := range []string{
		`[]`, `{}`, `{"items":null}`, `{"items":{}}`, `{"items":[null]}`,
		`{"items":[{}]}`, `{"items":[{"id":5,"kernel":"python"}]}`,
		`{"items":[{"id":"","kernel":"python"}]}`, `{"items":[{"id":"c"}]}`,
		`{"items":[{"id":"c","kernel":5}]}`, `{"items":[{"id":"c","kernel":""}]}`,
	} {
		t.Run(raw, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sessions": jsonHandler(t, json.RawMessage(raw)),
			})
			service := interpreterTestSandbox(t, fake).Interpreter()
			// When
			_, err := service.ListContexts(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
