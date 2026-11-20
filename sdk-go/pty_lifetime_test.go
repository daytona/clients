// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/require"
)

func TestPTYConnection_whenLifetimeEnds(t *testing.T) {
	for _, action := range []string{"cancel", "client close", "connection close"} {
		t.Run(action, func(t *testing.T) {
			// Given
			remoteClosed := make(chan struct{})
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /toolbox/sbx/processes/p/attach": ptyWebSocket(t, func(socket *websocket.Conn) {
					<-socket.CloseRead(t.Context()).Done()
					close(remoteClosed)
				}),
			})
			sandbox := newPTYTestSandbox(t, fake.Server)
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			connection, err := sandbox.PTY().Connect(ctx, "p")
			require.NoError(t, err)
			t.Cleanup(func() { require.NoError(t, connection.Close()) })
			readDone := make(chan error, 1)
			go func() {
				_, readErr := connection.Read(make([]byte, 1))
				readDone <- readErr
			}()
			// When
			switch action {
			case "cancel":
				cancel()
			case "client close":
				require.NoError(t, sandbox.client.Close())
			case "connection close":
				require.NoError(t, connection.Close())
				require.NoError(t, connection.Close())
			}
			// Then
			select {
			case err = <-readDone:
			case <-time.After(3 * time.Second):
				t.Fatal("read did not unblock")
			}
			if action == "client close" {
				require.ErrorIs(t, err, ErrClientClosed)
			} else {
				require.ErrorIs(t, err, context.Canceled)
			}
			select {
			case <-remoteClosed:
			case <-time.After(3 * time.Second):
				t.Fatal("socket was not closed")
			}
			_, err = connection.Write([]byte("closed"))
			require.Error(t, err)
		})
	}
}

func TestPTYConnection_whenIdleAndClientCloses(t *testing.T) {
	// Given
	remoteClosed := make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/processes/p/attach": ptyWebSocket(t, func(socket *websocket.Conn) {
			<-socket.CloseRead(t.Context()).Done()
			close(remoteClosed)
		}),
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	connection, err := sandbox.PTY().Connect(t.Context(), "p")
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, connection.Close()) })
	// When
	require.NoError(t, sandbox.client.Close())
	// Then
	select {
	case <-remoteClosed:
	case <-time.After(3 * time.Second):
		t.Fatal("idle socket was not closed")
	}
}

func TestPTYConnection_whenOpeningTimeoutHasStopped(t *testing.T) {
	// Given: a real WebSocket, with a synthetic clock only for the connection scope.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/processes/p/attach": ptyWebSocket(t, func(socket *websocket.Conn) {
			kind, data, err := socket.Read(t.Context())
			if err != nil {
				t.Error(err)
				return
			}
			if writeErr := socket.Write(t.Context(), kind, data); writeErr != nil {
				t.Error(writeErr)
			}
		}),
	})
	synctest.Test(t, func(t *testing.T) {
		sandbox := newPTYTestSandbox(t, fake.Server, WithRequestTimeout(time.Second))
		connection, err := sandbox.PTY().Connect(t.Context(), "p")
		require.NoError(t, err)
		defer func() { require.NoError(t, connection.Close()) }()
		// When: advance the scope's clock beyond the opening timeout.
		timer := time.NewTimer(2 * time.Second)
		<-timer.C
		n, err := connection.Write([]byte("x"))
		// Then
		require.NoError(t, err)
		require.Equal(t, 1, n)
		buf := make([]byte, 1)
		n, err = connection.Read(buf)
		require.NoError(t, err)
		require.Equal(t, 1, n)
		require.Equal(t, []byte("x"), buf)
	})
}
