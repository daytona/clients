// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/require"
)

func TestPTYAttach_whenInvalidIdentifier(t *testing.T) {
	for _, id := range []string{"", " ", ".", "..", string([]byte{255})} {
		t.Run(id, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := newPTYTestSandbox(t, fake.Server)
			// When
			connection, err := sandbox.PTY().Connect(t.Context(), id)
			// Then
			require.Nil(t, connection)
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestPTYAttach_whenKnownNonPTYKind(t *testing.T) {
	for _, kind := range []ProcessKind{ProcessKindExec, ProcessKindSession, ProcessKindCode} {
		t.Run(string(kind), func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := newPTYTestSandbox(t, fake.Server)
			handle, err := sandbox.Process().ConnectIdentity(ProcessHandleJSON{
				SandboxID: "sbx", ProcessID: "p", Kind: kind,
			})
			require.NoError(t, err)
			// When
			connection, err := handle.Attach(t.Context())
			// Then
			require.Nil(t, connection)
			require.ErrorIs(t, err, ErrUnsupportedOperation)
			require.Empty(t, fake.requests())
		})
	}
}

func TestPTYAttach_whenHandshakeIsRefused(t *testing.T) {
	for _, test := range []struct {
		name, media, body string
		status            int
		match             error
		code              string
	}{
		{"not found", "application/json", `{"code":"PROCESS_NOT_FOUND","source":"DAYTONA_DAEMON","message":"missing"}`, 404, ErrProcessNotFound, "PROCESS_NOT_FOUND"},
		{"forbidden", "application/json", `{"code":"FUTURE_CODE","source":"DAYTONA_DAEMON"}`, 403, ErrForbidden, "FUTURE_CODE"},
		{"plain text", "text/plain", `{"code":"PROCESS_NOT_FOUND"}`, 500, ErrInternalServer, ""},
		{"malformed JSON", "application/json", `{`, 404, ErrNotFound, ""},
		{"not an upgrade", "text/plain", "ok", 200, ErrConnection, ""},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /toolbox/sbx/processes/p/attach": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", test.media)
					w.WriteHeader(test.status)
					if _, err := w.Write([]byte(test.body)); err != nil {
						t.Error(err)
					}
				},
			})
			sandbox := newPTYTestSandbox(t, fake.Server)
			// When
			connection, err := sandbox.PTY().Connect(t.Context(), "p")
			// Then
			require.Nil(t, connection)
			require.ErrorIs(t, err, test.match)
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, test.code, sdkErr.Code)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestPTYAttach_whenRedirectIsRefused(t *testing.T) {
	// Given
	var followed atomic.Int32
	destination := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /": func(w http.ResponseWriter, _ *http.Request) {
			followed.Add(1)
			w.WriteHeader(http.StatusInternalServerError)
		},
	})
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/processes/p/attach": func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, destination.Server.URL, http.StatusTemporaryRedirect)
		},
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	// When
	connection, err := sandbox.PTY().Connect(t.Context(), "p")
	// Then
	require.Nil(t, connection)
	var sdkErr *Error
	require.ErrorAs(t, err, &sdkErr)
	require.Equal(t, http.StatusTemporaryRedirect, sdkErr.StatusCode)
	require.Equal(t, SourceSDK, sdkErr.Source)
	require.Zero(t, followed.Load())
}

func TestPTYAttach_whenOpeningTimesOut(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/processes/p/attach": func(_ http.ResponseWriter, r *http.Request) {
			<-r.Context().Done()
		},
	})
	sandbox := newPTYTestSandbox(t, fake.Server, WithRequestTimeout(20*time.Millisecond))
	// When
	connection, err := sandbox.PTY().Connect(t.Context(), "p")
	// Then
	require.Nil(t, connection)
	require.ErrorIs(t, err, ErrClientTimeout)
	require.ErrorIs(t, err, context.DeadlineExceeded)
}

func TestPTYAttach_whenClientAlreadyClosed(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	sandbox := newPTYTestSandbox(t, fake.Server)
	require.NoError(t, sandbox.client.Close())
	// When
	connection, err := sandbox.PTY().Connect(t.Context(), "p")
	// Then
	require.Nil(t, connection)
	require.ErrorIs(t, err, ErrClientClosed)
	require.Empty(t, fake.requests())
}

func TestPTYAttach_whenIdentifierNeedsEscaping(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/processes/{id}/attach": ptyWebSocket(t, func(socket *websocket.Conn) {
			<-socket.CloseRead(t.Context()).Done()
		}),
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	// When
	connection, err := sandbox.PTY().Connect(t.Context(), "space/slash ?")
	// Then
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, connection.Close()) })
	require.Equal(t, "/toolbox/sbx/processes/space%2Fslash%20%3F/attach", fake.requests()[0].Path)
}
