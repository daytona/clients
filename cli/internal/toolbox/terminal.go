// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"errors"
	"io"
	"net/url"
	"os"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/muesli/cancelreader"
	"golang.org/x/term"
)

// ProcessAttachment carries the canonical identity and replay position.
type ProcessAttachment struct {
	ID            string
	Cursor        string
	InitialFrames []tbgen.LogFrameDto
}

// StreamTerminal starts a PTY, drains its initial logs, then attaches with
// the server's cursor. Output written before or during attachment is replayed.
func (c *Client) StreamTerminal(ctx context.Context, request tbgen.CreateProcessRequest) (ProcessResult, error) {
	kind, stdin, retention := tbgen.Pty, tbgen.Open, tbgen.KeepLogs("until_cleanup")
	size := terminalSize(int(os.Stdout.Fd()))
	body := request
	body.Kind, body.Stdin, body.KeepLogs, body.Terminal = &kind, &stdin, &retention, &size
	proc, err := c.createStreamProcess(ctx, body, nil)
	if err != nil {
		return ProcessResult{}, err
	}
	result := ProcessResult{Process: proc}
	if ctx.Err() != nil {
		return result, ctx.Err()
	}
	// The cursor format is opaque. Obtain one from a beginning-of-log read,
	// rather than inventing an offset or attaching live and losing early bytes.
	page, err := c.ReadProcessLogs(ctx, proc.Id, url.Values{"encoding": {"base64"}})
	if err != nil {
		return result, err
	}
	result.Exit, err = c.AttachTerminal(ctx, ProcessAttachment{ID: proc.Id, Cursor: page.V.NextCursor, InitialFrames: page.V.Frames})
	return result, err
}

func terminalSize(fd int) tbgen.TerminalSize {
	cols, rows, err := term.GetSize(fd)
	if err != nil || cols <= 0 || rows <= 0 {
		cols, rows = 80, 24
	}
	return tbgen.TerminalSize{Cols: int32(cols), Rows: int32(rows)}
}

// AttachTerminal streams raw terminal bytes, restores the local terminal and
// waits using a canonical process id (live aliases disappear on exit).
func (c *Client) AttachTerminal(ctx context.Context, attachment ProcessAttachment) (tbgen.ExitResult, error) {
	ws, err := c.AttachWebSocket(ctx, attachment.ID, attachment.Cursor)
	if err != nil {
		return tbgen.ExitResult{}, err
	}
	defer ws.conn.Close()
	fd := int(os.Stdin.Fd())
	state, rawErr := term.MakeRaw(fd)
	if rawErr == nil {
		defer term.Restore(fd, state)
	}
	attachCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	stopClose := context.AfterFunc(attachCtx, func() {
		// Close the transport directly: a close-frame write can itself block.
		if err := ws.conn.Close(); err != nil {
			return
		}
	})
	defer stopClose()
	stopResize := c.forwardTerminalResizes(attachCtx, attachment.ID)
	defer stopResize()
	if err := WriteLogFrames(attachment.InitialFrames, "base64", os.Stdout, os.Stderr); err != nil {
		return tbgen.ExitResult{}, err
	}
	input, stopInput, err := terminalInput(os.Stdin)
	if err != nil {
		return tbgen.ExitResult{}, err
	}
	defer input.Close()
	inputDone := make(chan error, 1)
	go func() {
		inputErr := copyTerminalInput(ws, input)
		inputDone <- inputErr
		if inputErr != nil && !errors.Is(inputErr, cancelreader.ErrCanceled) {
			cancel()
		}
	}()
	defer func() {
		cancel()
		stopInput()
		<-inputDone
	}()
	for {
		msg, readErr := ws.ReadMessage()
		if errors.Is(readErr, io.EOF) {
			break
		}
		if readErr != nil {
			if ctx.Err() != nil {
				return tbgen.ExitResult{}, ctx.Err()
			}
			return tbgen.ExitResult{}, readErr
		}
		if _, err := os.Stdout.Write(msg); err != nil {
			return tbgen.ExitResult{}, err
		}
	}
	result, err := c.WaitProcess(ctx, attachment.ID, 0)
	return result.V, err
}

func terminalInput(file *os.File) (io.ReadCloser, func(), error) {
	info, err := file.Stat()
	if err != nil {
		return nil, nil, err
	}
	if info.Mode().IsRegular() || (info.Mode()&os.ModeCharDevice != 0 && !term.IsTerminal(int(file.Fd()))) {
		// Regular files and non-terminal devices (notably /dev/null) do not
		// support the terminal poller. They are used for noninteractive attach.
		return io.NopCloser(file), func() {}, nil
	}
	reader, err := cancelreader.NewReader(file)
	if err != nil {
		return nil, nil, err
	}
	return reader, func() {
		reader.Cancel()
	}, nil
}

func copyTerminalInput(ws *WebSocketConn, input io.Reader) error {
	buf := make([]byte, 32*1024)
	for {
		n, err := input.Read(buf)
		if n > 0 {
			if err := ws.WriteBinary(buf[:n]); err != nil {
				return err
			}
		}
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
	}
}
