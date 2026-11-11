// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestDialWebSocket_returnsContextError_whenHandshakeIsCanceled(t *testing.T) {
	// Given
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cancel()
		<-r.Context().Done()
	}))
	defer srv.Close()
	// When
	ws, err := dialWebSocket(ctx, srv.URL, nil)
	// Then
	require.Nil(t, ws)
	require.ErrorIs(t, err, context.Canceled)
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
}

func TestDialWebSocket_returnsContextError_whenUpgradeRacesCancellation(t *testing.T) {
	// Given
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, rw, err := w.(http.Hijacker).Hijack()
		if err != nil {
			return
		}
		defer conn.Close()
		cancel()
		_, _ = fmt.Fprintf(rw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n", wsAccept(r.Header.Get("Sec-WebSocket-Key")))
		_ = rw.Flush()
	}))
	defer srv.Close()
	// When
	ws, err := dialWebSocket(ctx, srv.URL, nil)
	// Then
	if ws != nil {
		defer ws.conn.Close()
	}
	require.Nil(t, ws)
	require.ErrorIs(t, err, context.Canceled)
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
}

func TestDialWebSocket_classifiesContextError_whenDialContextHasEnded(t *testing.T) {
	for _, deadline := range []bool{false, true} {
		t.Run(map[bool]string{false: "canceled", true: "deadline"}[deadline], func(t *testing.T) {
			// Given
			ctx, cancel := context.WithCancel(t.Context())
			cancel()
			wantError, wantExit := context.Canceled, cerr.ExitInterrupted
			if deadline {
				ctx, cancel = context.WithDeadline(t.Context(), time.Now().Add(-time.Second))
				defer cancel()
				wantError, wantExit = context.DeadlineExceeded, cerr.ExitTimeout
			}
			// When
			ws, err := dialWebSocket(ctx, "ws://127.0.0.1:1", nil)
			// Then
			require.Nil(t, ws)
			require.ErrorIs(t, err, wantError)
			require.Equal(t, wantExit, cerr.ExitCode(err))
		})
	}
}

func TestDialWebSocket_returnsTimeoutExit_whenHandshakeDeadlineExpires(t *testing.T) {
	// Given
	ctx, cancel := context.WithTimeout(t.Context(), 100*time.Millisecond)
	defer cancel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}))
	defer srv.Close()
	// When
	ws, err := dialWebSocket(ctx, srv.URL, nil)
	// Then
	require.Nil(t, ws)
	require.ErrorIs(t, err, context.DeadlineExceeded)
	require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
}

func TestDialWebSocket_keepsTransportOpen_whenContextCanceledAfterHandoff(t *testing.T) {
	// Given
	srv := wsEchoServer(t)
	defer srv.Close()
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	ws, err := dialWebSocket(ctx, srv.URL, nil)
	require.NoError(t, err)
	defer ws.conn.Close()
	// When
	cancel()
	require.NoError(t, ws.WriteBinary([]byte("still connected")))
	msg, err := ws.ReadMessage()
	// Then
	require.NoError(t, err)
	require.Equal(t, []byte("still connected"), msg)
}

func TestWebSocketReadMessage_onlyANormalCloseIsAnEndOfStream(t *testing.T) {
	for _, tc := range []struct {
		name    string
		payload []byte
		want    error
	}{
		{"no status", nil, nil},
		{"normal closure", []byte{0x03, 0xE8}, nil},
		{"normal closure with a reason", append([]byte{0x03, 0xE8}, "done"...), nil},
		{"policy close", append([]byte{0x03, 0xF0}, "cursor expired\x1b[2J"...), &WebSocketCloseError{Code: 1008, Reason: "cursor expired[2J"}},
		{"going away", []byte{0x03, 0xE9}, &WebSocketCloseError{Code: 1001}},
		{"truncated status", []byte{0x03}, &WebSocketCloseError{Code: 1002, Reason: "malformed close frame"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given a server that closes with this payload.
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				conn, rw, err := w.(http.Hijacker).Hijack()
				if err != nil {
					return
				}
				defer conn.Close()
				_, _ = fmt.Fprintf(rw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n", wsAccept(r.Header.Get("Sec-WebSocket-Key")))
				_ = rw.Flush()
				writeServerFrame(conn, 0x8, tc.payload)
			}))
			defer srv.Close()
			ws, err := dialWebSocket(t.Context(), srv.URL, nil)
			require.NoError(t, err)
			defer ws.conn.Close()
			// When
			_, err = ws.ReadMessage()
			// Then a normal close is the end of the stream, any other an error.
			if tc.want == nil {
				require.ErrorIs(t, err, io.EOF)
				return
			}
			require.Equal(t, tc.want, err)
			require.NotErrorIs(t, err, io.EOF)
		})
	}
}

func TestWebSocketReadMessage_distinguishesCloseFrameFromDroppedTransport(t *testing.T) {
	for _, tc := range []struct {
		name       string
		closeFrame bool
		want       error
	}{
		{"close frame", true, io.EOF},
		{"transport EOF", false, io.ErrUnexpectedEOF},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				conn, rw, err := w.(http.Hijacker).Hijack()
				if err != nil {
					return
				}
				defer conn.Close()
				_, _ = fmt.Fprintf(rw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n", wsAccept(r.Header.Get("Sec-WebSocket-Key")))
				_ = rw.Flush()
				if tc.closeFrame {
					writeServerFrame(conn, 0x8, nil)
				}
			}))
			defer srv.Close()
			ws, err := dialWebSocket(t.Context(), srv.URL, nil)
			require.NoError(t, err)
			defer ws.conn.Close()
			// When
			_, err = ws.ReadMessage()
			// Then
			require.ErrorIs(t, err, tc.want)
		})
	}
}
