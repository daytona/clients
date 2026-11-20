// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"sync"

	"github.com/coder/websocket"
)

// PTYConnection is a terminal's live byte stream, safe for concurrent use.
// It implements io.ReadWriteCloser. Read, Write and Close intentionally take no
// context to satisfy those interfaces: the context passed to Attach, Connect
// or Create controls their lifetime, and Client.Close cancels them too.
// Keep reading to process terminal output and connection control frames.
// Close releases the connection without killing or deleting the process.
type PTYConnection struct {
	handle    *ProcessHandle
	socket    *websocket.Conn
	scope     *requestScope
	stop      func() bool
	closeOnce sync.Once
	closeErr  error
	readMu    sync.Mutex
	reader    io.Reader
	readErr   error
}

var _ io.ReadWriteCloser = (*PTYConnection)(nil)

// Attach connects to this process's terminal. Known non-PTY kinds return
// ErrUnsupportedOperation; an ID-only handle lets the server decide.
// The request timeout bounds opening only, while ctx and Client.Close bound
// the connection's entire lifetime. The caller must close the connection.
func (h *ProcessHandle) Attach(ctx context.Context) (*PTYConnection, error) {
	switch h.kind {
	case ProcessKindExec, ProcessKindSession, ProcessKindCode:
		return nil, newSDKError(CodeUnsupportedOperation, 400, "attach is only supported for pty handles")
	case "", ProcessKindUnknown, ProcessKindPTY:
		// ID-only handles let the server validate the process kind.
	default:
		return nil, newSDKError(CodeUnsupportedOperation, 400, "attach is only supported for pty handles")
	}
	segment, err := pathSegment(h.id)
	if err != nil {
		return nil, err
	}
	scoped, err := h.sandbox.toolbox().scoped()
	if err != nil {
		return nil, err
	}
	requestCtx, scope := newRequestScope(ctx, h.sandbox.client, h.sandbox.client.config.timeout)
	scope.kick()
	base, headers, err := h.sandbox.toolbox().connectionInfo(requestCtx)
	if err != nil {
		scope.close()
		return nil, err
	}
	endpoint, err := relativeURL(base, "/processes/"+segment+"/attach", nil)
	if err != nil {
		scope.close()
		return nil, err
	}
	socket, response, err := websocket.Dial(requestCtx, endpoint, &websocket.DialOptions{
		HTTPClient: h.sandbox.client.http, HTTPHeader: headers,
	})
	if err != nil {
		failure := transportError(requestCtx, err)
		if response != nil && response.StatusCode >= http.StatusMultipleChoices && requestCtx.Err() == nil {
			failure = scoped.responseFailure(requestCtx, response, 0)
		} else if response != nil && response.Body != nil {
			closeResponse(requestCtx, response, &failure)
		}
		scope.close()
		return nil, failure
	}
	scope.pause()
	// Reader streams into the caller's buffer, so a large frame needs no whole-message allocation.
	socket.SetReadLimit(-1)
	connection := &PTYConnection{handle: h, socket: socket, scope: scope}
	connection.stop = context.AfterFunc(requestCtx, func() { connection.closeOnce.Do(connection.closeSocket) })
	return connection, nil
}

// Read reads terminal bytes across binary and text message boundaries.
// A normal remote close returns io.EOF; abnormal closure is an SDK error.
func (c *PTYConnection) Read(data []byte) (int, error) {
	c.readMu.Lock()
	defer c.readMu.Unlock()
	if len(data) == 0 {
		return 0, nil
	}
	for {
		if c.readErr != nil {
			return 0, c.readErr
		}
		if c.scope.ctx.Err() != nil {
			c.readErr = transportError(c.scope.ctx, c.scope.ctx.Err())
			return 0, c.readErr
		}
		if c.reader == nil {
			_, reader, err := c.socket.Reader(c.scope.ctx)
			if err != nil {
				c.readErr = c.readFailure(err)
				return 0, c.readErr
			}
			c.reader = reader
		}
		n, err := c.reader.Read(data)
		if errors.Is(err, io.EOF) {
			c.reader = nil
			if n > 0 {
				return n, nil
			}
			continue
		}
		if err != nil {
			c.readErr = c.readFailure(err)
		}
		return n, c.readErr
	}
}

func (c *PTYConnection) readFailure(err error) error {
	status := websocket.CloseStatus(err)
	if c.scope.ctx.Err() == nil &&
		(status == websocket.StatusNormalClosure || status == websocket.StatusGoingAway) {
		return io.EOF
	}
	return transportError(c.scope.ctx, err)
}

// Write sends exact bytes to the terminal, bounded by the attach context.
func (c *PTYConnection) Write(data []byte) (int, error) {
	if err := c.socket.Write(c.scope.ctx, websocket.MessageBinary, data); err != nil {
		return 0, transportError(c.scope.ctx, err)
	}
	return len(data), nil
}

// Close immediately disconnects and unblocks pending I/O without waiting for a
// remote close handshake. It is idempotent and does not stop the process.
func (c *PTYConnection) Close() error {
	c.stop()
	c.scope.close()
	c.closeOnce.Do(c.closeSocket)
	return c.closeErr
}

func (c *PTYConnection) closeSocket() {
	if err := c.socket.CloseNow(); err != nil && !errors.Is(err, net.ErrClosed) {
		c.closeErr = transportError(c.scope.ctx, err)
	}
}

// Resize changes the terminal's dimensions using the supplied request context.
func (c *PTYConnection) Resize(ctx context.Context, size TerminalSize) error {
	return c.handle.Resize(ctx, size)
}

// Handle returns the process handle for metadata, signals and deletion.
func (c *PTYConnection) Handle() *ProcessHandle { return c.handle }
