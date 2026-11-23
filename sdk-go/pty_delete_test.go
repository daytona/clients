// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestPTYDelete_whenKindIsNotPTY(t *testing.T) {
	for _, kind := range []string{"exec", "session", "code", "future-kind"} {
		t.Run(kind, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /toolbox/sbx/processes/p": jsonHandler(t, ptyFixture(kind)),
			})
			sandbox := newPTYTestSandbox(t, fake.Server)
			// When
			err := sandbox.PTY().Delete(t.Context(), "p")
			// Then
			require.ErrorIs(t, err, ErrUnsupportedOperation)
			require.Len(t, fake.requests(), 1)
			require.Equal(t, http.MethodGet, fake.requests()[0].Method)
		})
	}
}

func TestPTYDelete_whenProcessIsRunningOrTerminal(t *testing.T) {
	for _, terminal := range []bool{false, true} {
		name := "running"
		if terminal {
			name = "terminal"
		}
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /toolbox/sbx/processes/p": jsonHandler(t, ptyFixture("pty")),
				"POST /toolbox/sbx/processes/p/signals": func(w http.ResponseWriter, _ *http.Request) {
					if terminal {
						w.Header().Set("Content-Type", "application/json")
						w.WriteHeader(http.StatusConflict)
						writeJSON(t, w, map[string]string{"code": "PROCESS_TERMINAL", "source": SourceDaemon})
						return
					}
					w.WriteHeader(http.StatusNoContent)
				},
				"DELETE /toolbox/sbx/processes/p": func(w http.ResponseWriter, _ *http.Request) {
					w.WriteHeader(http.StatusNoContent)
				},
			})
			sandbox := newPTYTestSandbox(t, fake.Server)
			// When
			err := sandbox.PTY().Delete(t.Context(), "p")
			// Then
			require.NoError(t, err)
			requests := fake.requests()
			require.Len(t, requests, 3)
			require.JSONEq(t, `{"signal":"SIGTERM"}`, string(requests[1].Body))
			require.Equal(t, http.MethodDelete, requests[2].Method)
		})
	}
}

func TestPTYDelete_whenSignalFails(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/processes/p": jsonHandler(t, ptyFixture("pty")),
		"POST /toolbox/sbx/processes/p/signals": func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusForbidden)
		},
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	// When
	err := sandbox.PTY().Delete(t.Context(), "p")
	// Then
	require.ErrorIs(t, err, ErrForbidden)
	require.Len(t, fake.requests(), 2)
}

func TestPTYCreate_whenAttachFailsCleansUp(t *testing.T) {
	for _, cleanupStatus := range []int{http.StatusNoContent, http.StatusInternalServerError} {
		t.Run(http.StatusText(cleanupStatus), func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /toolbox/sbx/processes": jsonHandler(t, ptyFixture("pty")),
				"GET /toolbox/sbx/processes/p/attach": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusNotFound)
					writeJSON(t, w, map[string]string{"code": "PROCESS_NOT_FOUND", "source": SourceDaemon})
				},
				"DELETE /toolbox/sbx/processes/p": func(w http.ResponseWriter, _ *http.Request) {
					w.WriteHeader(cleanupStatus)
				},
			})
			sandbox := newPTYTestSandbox(t, fake.Server)
			// When
			connection, err := sandbox.PTY().Create(t.Context(), "", nil)
			// Then
			require.Nil(t, connection)
			require.ErrorIs(t, err, ErrProcessNotFound)
			require.NotErrorIs(t, err, ErrInternalServer)
			requests := fake.requests()
			require.Len(t, requests, 3)
			require.Equal(t, http.MethodDelete, requests[2].Method)
		})
	}
}
