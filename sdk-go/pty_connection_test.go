// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/require"
)

func ptyWebSocket(t *testing.T, handler func(*websocket.Conn)) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, r *http.Request) {
		socket, err := websocket.Accept(w, r, nil)
		if err != nil {
			t.Error(err)
			return
		}
		defer func() {
			if closeErr := socket.CloseNow(); closeErr != nil && !errors.Is(closeErr, net.ErrClosed) {
				t.Error(closeErr)
			}
		}()
		handler(socket)
	}
}

func TestPTYConnectionRead_whenMixedFramesAndLargeMessages(t *testing.T) {
	// Given
	large := strings.Repeat("x", 128*1024)
	serverDone := make(chan error, 1)
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/processes/p/attach": ptyWebSocket(t, func(socket *websocket.Conn) {
			for _, frame := range []struct {
				kind websocket.MessageType
				data []byte
			}{
				{websocket.MessageBinary, []byte{0, 255, 1}},
				{websocket.MessageText, []byte("héllo")},
				{websocket.MessageBinary, nil},
				{websocket.MessageBinary, []byte(large)},
			} {
				if err := socket.Write(t.Context(), frame.kind, frame.data); err != nil {
					serverDone <- err
					return
				}
			}
			serverDone <- socket.Close(websocket.StatusNormalClosure, "")
		}),
	})
	sandbox := newPTYTestSandbox(t, fake.Server, WithMaxResponseBytes(1))
	connection, err := sandbox.PTY().Connect(t.Context(), "p")
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, connection.Close()) })
	// When
	var output strings.Builder
	_, err = io.CopyBuffer(&output, connection, make([]byte, 7))
	// Then
	require.NoError(t, err)
	require.NoError(t, <-serverDone)
	require.Equal(t, string([]byte{0, 255, 1})+"héllo"+large, output.String())
	buffer := make([]byte, 1)
	_, err = connection.Read(buffer)
	require.ErrorIs(t, err, io.EOF)
}

func TestPTYConnectionWrite_whenSendingBinaryBytes(t *testing.T) {
	// Given
	type received struct {
		kind websocket.MessageType
		data []byte
		err  error
	}
	messages := make(chan received, 1)
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/processes/p/attach": ptyWebSocket(t, func(socket *websocket.Conn) {
			kind, data, err := socket.Read(t.Context())
			messages <- received{kind, data, err}
		}),
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	connection, err := sandbox.PTY().Connect(t.Context(), "p")
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, connection.Close()) })
	input := []byte{0, 255, '\n'}
	// When
	n, err := connection.Write(input)
	// Then
	require.NoError(t, err)
	require.Equal(t, len(input), n)
	message := <-messages
	require.NoError(t, message.err)
	require.Equal(t, websocket.MessageBinary, message.kind)
	require.Equal(t, input, message.data)
}

type ptyTestTransport struct {
	next  http.RoundTripper
	calls atomic.Int32
}

func (tr *ptyTestTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	tr.calls.Add(1)
	request.Header.Set("X-Custom-Transport", "preserved")
	return tr.next.RoundTrip(request)
}

func TestPTYCreate_whenPreservingTransportAndHeaders(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /toolbox/sbx/processes": jsonHandler(t, ptyFixture("pty")),
		"GET /toolbox/sbx/processes/p/attach": ptyWebSocket(t, func(socket *websocket.Conn) {
			<-socket.CloseRead(t.Context()).Done()
		}),
	})
	injected := &ptyTestTransport{next: fake.Server.Client().Transport}
	sandbox := newPTYTestSandbox(t, fake.Server, WithHTTPClient(&http.Client{Transport: injected}))
	// When
	connection, err := sandbox.PTY().Create(t.Context(), "", nil)
	// Then
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, connection.Close()) })
	require.Equal(t, "p", connection.Handle().ID())
	require.EqualValues(t, 2, injected.calls.Load())
	request := fake.requests()[1]
	require.Equal(t, "Bearer terminal-caller", request.Header.Get("Authorization"))
	require.Equal(t, "sdk-go", request.Header.Get("X-Daytona-Source"))
	require.Equal(t, sandbox.client.version, request.Header.Get("X-Daytona-SDK-Version"))
	require.Equal(t, "sdk-go/"+sandbox.client.version, request.Header.Get("User-Agent"))
	require.Equal(t, "preserved", request.Header.Get("X-Custom-Transport"))
	require.Empty(t, request.Query.Get("daytona_token"))
}

func TestPTYConnectionResize_whenChangingDimensions(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/processes/p/attach": ptyWebSocket(t, func(socket *websocket.Conn) {
			<-socket.CloseRead(t.Context()).Done()
		}),
		"POST /toolbox/sbx/processes/p/resize": func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusNoContent)
		},
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	connection, err := sandbox.PTY().Connect(t.Context(), "p")
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, connection.Close()) })
	// When
	err = connection.Resize(t.Context(), TerminalSize{Cols: 100, Rows: 50})
	// Then
	require.NoError(t, err)
	require.JSONEq(t, `{"cols":100,"rows":50}`, string(fake.requests()[1].Body))
}

func TestPTYConnectionRead_whenRemoteClosesAbnormally(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/processes/p/attach": ptyWebSocket(t, func(socket *websocket.Conn) {
			if err := socket.Close(websocket.StatusPolicyViolation, "refused"); err != nil {
				t.Error(err)
			}
		}),
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	connection, err := sandbox.PTY().Connect(t.Context(), "p")
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, connection.Close()) })
	// When
	_, err = connection.Read(make([]byte, 10))
	// Then
	require.ErrorIs(t, err, ErrConnection)
	require.NotErrorIs(t, err, io.EOF)
	var closed websocket.CloseError
	require.ErrorAs(t, err, &closed)
	require.Equal(t, websocket.StatusPolicyViolation, closed.Code)
}

func TestPTYCreate_whenCancellationStillCleansUp(t *testing.T) {
	// Given
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /toolbox/sbx/processes": jsonHandler(t, ptyFixture("pty")),
		"GET /toolbox/sbx/processes/p/attach": func(_ http.ResponseWriter, r *http.Request) {
			cancel()
			<-r.Context().Done()
		},
		"DELETE /toolbox/sbx/processes/p": func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusNoContent)
		},
	})
	sandbox := newPTYTestSandbox(t, fake.Server)
	// When
	connection, err := sandbox.PTY().Create(ctx, "", nil)
	// Then
	require.Nil(t, connection)
	require.ErrorIs(t, err, context.Canceled)
	requests := fake.requests()
	require.Len(t, requests, 3)
	require.Equal(t, http.MethodDelete, requests[2].Method)
}
