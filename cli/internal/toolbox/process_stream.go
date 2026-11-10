// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"strings"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
)

const inlineWait = time.Second
const followLimit = 256
const stdinChunkSize = 32 * 1024

type StreamProcessOptions struct {
	Request     tbgen.CreateProcessRequest
	Timeout     time.Duration
	Stdout      io.Writer
	Stderr      io.Writer
	Stdin       io.Reader
	LogEncoding string
}

type ProcessResult struct {
	Process tbgen.ProcessDto
	Exit    tbgen.ExitResult
}

func (c *Client) StreamProcess(ctx context.Context, opts StreamProcessOptions) (_ ProcessResult, err error) {
	encoding := opts.LogEncoding
	if encoding == "" {
		encoding = "text"
	}
	inline := inlineWait
	if opts.Timeout > 0 && opts.Timeout < inline {
		inline = opts.Timeout
	}
	query := url.Values{}
	query.Set("waitMs", fmt.Sprintf("%d", inline.Milliseconds()))
	query.Set("logs", encoding)
	process, err := c.CreateProcess(ctx, opts.Request, query)
	if err != nil {
		return ProcessResult{}, err
	}
	// Retain the identity even on a later stream failure so callers can
	// interrupt the process when their own invocation is cancelled.
	created := ProcessResult{Process: process}
	var pump *stdinPump
	// Feed stdin only while the process can still read it. A command that
	// exited within the inline wait (most one-shot commands) needs no pump —
	// and a late write would answer 409 PROCESS_TERMINAL, which is not an
	// error of the command; pumpStdin treats that code as end-of-input too.
	if opts.Stdin != nil && process.State != tbgen.StateTerminal {
		pump = c.startStdinPump(ctx, process.Id, opts.Stdin)
		defer func() {
			// On failure stop feeding stdin first: the process's output is
			// no longer followed, and waiting for the upload could stall.
			if err != nil {
				pump.cancel()
			}
			err = errors.Join(err, pump.finish())
			pump.cancel()
		}()
	}
	cursor := ""
	if process.Logs != nil {
		cursor = process.Logs.NextCursor
		if err := WriteLogFrames(process.Logs.Frames, encoding, opts.Stdout, opts.Stderr); err != nil {
			return created, err
		}
	}
	result := resultFromProcess(process)
	eof := process.Logs != nil && process.Logs.Eof
	if process.State != tbgen.StateTerminal || !eof {
		followResult, followCursor, followEOF, err := c.FollowProcessLogs(ctx, process.Id, cursor, encoding, 0, opts.Stdout, opts.Stderr)
		if err != nil {
			return created, err
		}
		cursor = followCursor
		eof = followEOF
		if followResult.Reason != nil {
			result = followResult
		}
	}
	if result.Reason == nil || !eof {
		pagedResult, err := c.finishProcessLogs(ctx, process.Id, cursor, encoding, opts.Stdout, opts.Stderr, result, opts.Timeout)
		if err != nil {
			return created, err
		}
		result = pagedResult
	}
	return ProcessResult{Process: process, Exit: normalizeExit(result)}, nil
}

type stdinPump struct {
	cancel  context.CancelFunc
	closer  io.Closer
	done    chan error
	regular bool
}

func (c *Client) startStdinPump(ctx context.Context, processID string, reader io.Reader) *stdinPump {
	pumpCtx, cancel := context.WithCancel(ctx)
	p := &stdinPump{cancel: cancel, done: make(chan error, 1)}
	if closer, ok := reader.(io.Closer); ok {
		p.closer = closer
	}
	if file, ok := reader.(*os.File); ok {
		if info, err := file.Stat(); err == nil && info.Mode().IsRegular() {
			p.regular = true
		}
	}
	go func() {
		p.done <- c.pumpStdin(pumpCtx, processID, reader)
	}()
	return p
}

func (p *stdinPump) finish() error {
	select {
	case err := <-p.done:
		return ignoreExpectedPumpShutdown(err)
	default:
	}
	if p.regular {
		return ignoreExpectedPumpShutdown(<-p.done)
	}
	if p.closer != nil {
		_ = p.closer.Close()
		return ignoreExpectedPumpShutdown(<-p.done)
	}
	p.cancel()
	select {
	case err := <-p.done:
		return ignoreExpectedPumpShutdown(err)
	default:
		return nil
	}
}

func ignoreExpectedPumpShutdown(err error) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, io.ErrClosedPipe) || errors.Is(err, os.ErrClosed) {
		return nil
	}
	return err
}

func (c *Client) finishProcessLogs(ctx context.Context, processID, cursor, encoding string, stdout, stderr io.Writer, result tbgen.ExitResult, timeout time.Duration) (tbgen.ExitResult, error) {
	if result.Reason == nil {
		waitResult, err := c.WaitProcess(ctx, processID, timeout)
		if err != nil {
			return tbgen.ExitResult{}, err
		}
		result = waitResult.V
	}
	for {
		query := url.Values{}
		if cursor != "" {
			query.Set("cursor", cursor)
		}
		query.Set("encoding", encoding)
		page, err := c.ReadProcessLogs(ctx, processID, query)
		if err != nil {
			return tbgen.ExitResult{}, err
		}
		if err := WriteLogFrames(page.V.Frames, encoding, stdout, stderr); err != nil {
			return tbgen.ExitResult{}, err
		}
		if page.V.Eof || page.V.NextCursor == "" || page.V.NextCursor == cursor || len(page.V.Frames) == 0 {
			return result, nil
		}
		cursor = page.V.NextCursor
	}
}

func resolveFollowLimit(limit int) int {
	if limit > 0 {
		return limit
	}
	return followLimit
}

func (c *Client) pumpStdin(ctx context.Context, processID string, r io.Reader) error {
	buf := make([]byte, stdinChunkSize)
	for {
		n, err := r.Read(buf)
		if n > 0 {
			if writeErr := c.WriteStdin(ctx, processID, buf[:n], false); writeErr != nil {
				if IsDaemonCode(writeErr, api.CodeProcessTerminal) {
					return nil // the process finished without reading the rest
				}
				return writeErr
			}
		}
		if err == io.EOF {
			if closeErr := c.WriteStdin(ctx, processID, nil, true); closeErr != nil && !IsDaemonCode(closeErr, api.CodeProcessTerminal) {
				return closeErr
			}
			return nil
		}
		if err != nil {
			return fmt.Errorf("read stdin: %w", err)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
	}
}

func WriteLogFrames(frames []tbgen.LogFrameDto, encoding string, stdout, stderr io.Writer) error {
	for _, frame := range frames {
		data, err := decodeFrameData(frame.Data, encoding)
		if err != nil {
			return err
		}
		target := stdout
		if frame.Channel == "stderr" || frame.Channel == "error" {
			target = stderr
		}
		if target == nil {
			continue
		}
		if _, err := target.Write(data); err != nil {
			return fmt.Errorf("write process output: %w", err)
		}
	}
	return nil
}

// decodeFrameData accepts log frames with either base64 alphabet, padded or
// unpadded, including base64url as defined in RFC 4648 section 5.
func decodeFrameData(raw, encoding string) ([]byte, error) {
	if encoding != "base64" {
		return []byte(raw), nil
	}
	trimmed := strings.TrimRight(raw, "=")
	enc := base64.RawURLEncoding
	if strings.ContainsAny(trimmed, "+/") {
		enc = base64.RawStdEncoding
	}
	data, err := enc.DecodeString(trimmed)
	if err != nil {
		return nil, fmt.Errorf("decode base64 log frame: %w", err)
	}
	return data, nil
}

func resultFromProcess(p tbgen.ProcessDto) tbgen.ExitResult {
	return normalizeExit(tbgen.ExitResult{ExitCode: p.ExitCode, Reason: p.Reason, Signal: p.Signal})
}
