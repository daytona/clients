// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
)

var ErrProcessLogsStreamEndedEarly = errors.New("stream ended before the process finished")

type followProcessLogsOptions struct {
	processID string
	cursor    string
	encoding  string
	limit     int
	onFrame   func(api.Raw[tbgen.LogFrameDto]) error
	onWarning func(LogWarning)
}

type processStateEvent struct {
	Cursor   string        `json:"cursor"`
	State    tbgen.State   `json:"state"`
	Reason   *tbgen.Reason `json:"reason,omitempty"`
	ExitCode *int32        `json:"exitCode,omitempty"`
	Signal   *string       `json:"signal,omitempty"`
}

type processEOFEvent struct {
	Cursor string `json:"cursor"`
}

type processErrorEvent struct {
	Cursor  string `json:"cursor"`
	Message string `json:"message"`
}

type processWarningEvent struct {
	Cursor               string `json:"cursor"`
	FirstAvailableCursor string `json:"firstAvailableCursor"`
	Message              string `json:"message"`
}

// LogWarning is a server-side `warning` event: the requested cursor expired
// and frames were lost; FirstAvailableCursor is where the stream resumed.
type LogWarning struct {
	Cursor               string
	FirstAvailableCursor string
	Message              string
}

func (c *Client) FollowProcessLogFrames(ctx context.Context, processID, cursor, encoding string, limit int, onFrame func(api.Raw[tbgen.LogFrameDto]) error) (tbgen.ExitResult, string, bool, error) {
	return c.followProcessLogs(ctx, followProcessLogsOptions{
		processID: processID,
		cursor:    cursor,
		encoding:  encoding,
		limit:     limit,
		onFrame:   onFrame,
		onWarning: c.OnLogWarning,
	})
}

func (c *Client) FollowProcessLogs(ctx context.Context, processID, cursor, encoding string, limit int, stdout, stderr io.Writer) (tbgen.ExitResult, string, bool, error) {
	return c.followProcessLogs(ctx, followProcessLogsOptions{
		processID: processID,
		cursor:    cursor,
		encoding:  encoding,
		limit:     limit,
		onFrame: func(frame api.Raw[tbgen.LogFrameDto]) error {
			return WriteLogFrames([]tbgen.LogFrameDto{frame.V}, encoding, stdout, stderr)
		},
		onWarning: c.OnLogWarning,
	})
}

func (c *Client) followProcessLogs(ctx context.Context, opts followProcessLogsOptions) (tbgen.ExitResult, string, bool, error) {
	query := url.Values{}
	if opts.cursor != "" {
		query.Set("cursor", opts.cursor)
	}
	query.Set("encoding", opts.encoding)
	query.Set("follow", "true")
	query.Set("limit", fmt.Sprintf("%d", resolveFollowLimit(opts.limit)))
	resp, err := c.Stream(ctx, http.MethodGet, "/processes/"+url.PathEscape(opts.processID)+"/logs", query, nil, "")
	if err != nil {
		return tbgen.ExitResult{}, opts.cursor, false, err
	}
	defer resp.Body.Close()

	result := tbgen.ExitResult{}
	lastCursor := opts.cursor
	eof := false
	terminalStateSeen := false
	err = readSSE(resp.Body, func(msg sseMessage) error {
		if msg.ID != "" {
			lastCursor = msg.ID
		}
		switch msg.Event {
		case "log", "message":
			var frame api.Raw[tbgen.LogFrameDto]
			if err := json.Unmarshal([]byte(msg.Data), &frame); err != nil {
				return fmt.Errorf("decode log frame: %w", err)
			}
			if frame.V.Cursor != "" {
				lastCursor = frame.V.Cursor
			}
			return opts.onFrame(frame)
		case "state":
			var event processStateEvent
			if err := json.Unmarshal([]byte(msg.Data), &event); err != nil {
				return fmt.Errorf("decode wait result: %w", err)
			}
			if event.Cursor != "" {
				lastCursor = event.Cursor
			}
			terminalStateSeen = event.State == tbgen.StateTerminal || event.Reason != nil
			result = normalizeExit(tbgen.ExitResult{ExitCode: event.ExitCode, Reason: event.Reason, Signal: event.Signal})
			return nil
		case "warning":
			// The requested cursor expired: frames between it and
			// firstAvailableCursor are gone. Resume from the surviving
			// position and tell the caller — silence would make a gap look
			// like a quiet process.
			var payload processWarningEvent
			if err := json.Unmarshal([]byte(msg.Data), &payload); err == nil {
				if payload.FirstAvailableCursor != "" {
					lastCursor = payload.FirstAvailableCursor
				} else if payload.Cursor != "" {
					lastCursor = payload.Cursor
				}
				if opts.onWarning != nil {
					opts.onWarning(LogWarning{Cursor: payload.Cursor, FirstAvailableCursor: payload.FirstAvailableCursor, Message: payload.Message})
				}
			}
			return nil
		case "eof":
			var payload processEOFEvent
			if err := json.Unmarshal([]byte(msg.Data), &payload); err != nil {
				return fmt.Errorf("decode SSE eof: %w", err)
			}
			if payload.Cursor != "" {
				lastCursor = payload.Cursor
			}
			eof = true
			return nil
		case "error":
			var payload processErrorEvent
			if err := json.Unmarshal([]byte(msg.Data), &payload); err != nil {
				return fmt.Errorf("decode SSE error: %w", err)
			}
			if payload.Cursor != "" {
				lastCursor = payload.Cursor
			}
			if payload.Message == "" {
				payload.Message = msg.Data
			}
			return fmt.Errorf("%s", payload.Message)
		default:
			return nil
		}
	})
	if err != nil {
		if errors.Is(ctx.Err(), context.Canceled) {
			return tbgen.ExitResult{}, lastCursor, eof, cerr.New(cerr.ExitInterrupted, "interrupted")
		}
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return tbgen.ExitResult{}, lastCursor, eof, ctx.Err()
		}
		return tbgen.ExitResult{}, lastCursor, eof, err
	}
	if !eof && !terminalStateSeen {
		return tbgen.ExitResult{}, lastCursor, false, ErrProcessLogsStreamEndedEarly
	}
	return result, lastCursor, eof, nil
}
