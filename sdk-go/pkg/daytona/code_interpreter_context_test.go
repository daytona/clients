// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func newScopedInterpreterTestServer(t *testing.T) (*httptest.Server, *bool) {
	t.Helper()
	deleted := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			deleted = true
			w.WriteHeader(http.StatusNoContent)
			return
		}
		writeJSONResponse(t, w, http.StatusOK, map[string]any{
			"id": "ctx-scoped", "cwd": "/tmp", "language": "python", "active": true,
			"createdAt": "2026-08-13T00:00:00Z",
		})
	}))
	return server, &deleted
}

func TestCodeInterpreterWithContext_deletes_context_after_callback_error(t *testing.T) {
	// Given
	server, deleted := newScopedInterpreterTestServer(t)
	defer server.Close()
	interpreter := NewCodeInterpreterService(createTestToolboxClient(server), nil)

	// When
	err := interpreter.WithContext(context.Background(), func(interpreterContext InterpreterContext) error {
		require.Equal(t, "ctx-scoped", interpreterContext.Id)
		return assert.AnError
	})

	// Then
	require.ErrorIs(t, err, assert.AnError)
	require.True(t, *deleted)
}

func TestCodeInterpreterWithContext_deletes_context_after_callback_panic(t *testing.T) {
	// Given
	server, deleted := newScopedInterpreterTestServer(t)
	defer server.Close()
	interpreter := NewCodeInterpreterService(createTestToolboxClient(server), nil)
	var recovered any

	// When
	func() {
		defer func() { recovered = recover() }()
		_ = interpreter.WithContext(context.Background(), func(InterpreterContext) error {
			panic("callback panic")
		})
	}()

	// Then
	require.Equal(t, "callback panic", recovered)
	require.True(t, *deleted)
}
