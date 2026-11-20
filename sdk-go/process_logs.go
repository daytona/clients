// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"errors"
	"iter"
	"net/url"
	"strconv"
	"time"
)

// LogPage reads one retained log page. ErrCursorExpired includes the server's
// first available cursor. The result owns its frames.
func (h *ProcessHandle) LogPage(ctx context.Context, opts *ProcessLogsOptions) (*ProcessLogPage, error) {
	query, err := processLogQuery(opts)
	if err != nil {
		return nil, err
	}
	var raw json.RawMessage
	if err = h.request(ctx, toolboxRequest{Path: "/logs", Query: query, Result: &raw}); err != nil {
		return nil, err
	}
	return parseProcessLogPage(raw, processLogEncoding(opts))
}

// Logs follows replayed and live log frames until EOF. For terminal-state and
// retention-warning events use StreamLogs. Breaking iteration closes only this stream.
func (h *ProcessHandle) Logs(ctx context.Context, opts *ProcessLogsOptions) iter.Seq2[LogFrame, error] {
	events := h.StreamLogs(ctx, opts)
	return func(yield func(LogFrame, error) bool) {
		for event, err := range events {
			if err != nil {
				yield(LogFrame{}, err)
				return
			}
			switch value := event.(type) {
			case ProcessLogFrameEvent:
				if !yield(value.Frame, nil) {
					return
				}
			case ProcessLogStateEvent, ProcessLogWarningEvent, ProcessLogEOFEvent:
				// The frame-only view intentionally omits control events.
			}
		}
	}
}

func processLogQuery(opts *ProcessLogsOptions) (url.Values, error) {
	query := make(url.Values)
	if opts == nil {
		return query, nil
	}
	if opts.Limit < 0 {
		return nil, badRequest("Limit must be non-negative")
	}
	if opts.Encoding != "" && opts.Encoding != LogEncodingText && opts.Encoding != LogEncodingBase64 {
		return nil, badRequest("Encoding must be text or base64")
	}
	if opts.Cursor != "" {
		query.Set("cursor", opts.Cursor)
	}
	if opts.Limit > 0 {
		query.Set("limit", strconv.Itoa(opts.Limit))
	}
	if opts.Encoding != "" {
		query.Set("encoding", string(opts.Encoding))
	}
	return query, nil
}

func processLogEncoding(opts *ProcessLogsOptions) LogEncoding {
	if opts != nil && opts.Encoding != "" {
		return opts.Encoding
	}
	return LogEncodingText
}

func parseProcessLogPage(raw json.RawMessage, encoding LogEncoding) (*ProcessLogPage, error) {
	object, err := parseObject(raw, "process log page")
	if err != nil {
		return nil, err
	}
	frames, err := field[[]json.RawMessage](object, "frames", true)
	if err != nil {
		return nil, err
	}
	cursor, cursorErr := field[string](object, "nextCursor", true)
	truncated, truncatedErr := field[bool](object, "truncatedHead", true)
	eof, eofErr := field[bool](object, "eof", true)
	first, firstErr := field[string](object, "firstAvailableCursor", false)
	if err = errors.Join(cursorErr, truncatedErr, eofErr, firstErr); err != nil {
		return nil, err
	}
	result := &ProcessLogPage{
		Items: make([]LogFrame, 0, len(frames)), NextCursor: cursor, TruncatedHead: truncated,
		EOF: eof, FirstAvailableCursor: first,
	}
	for _, rawFrame := range frames {
		frame, parseErr := parseProcessFrame(rawFrame, encoding)
		if parseErr != nil {
			return nil, parseErr
		}
		result.Items = append(result.Items, frame)
	}
	return result, nil
}

func parseProcessFrame(raw json.RawMessage, encoding LogEncoding) (LogFrame, error) {
	object, err := parseObject(raw, "log frame")
	if err != nil {
		return LogFrame{}, err
	}
	cursor, cursorErr := field[string](object, "cursor", true)
	channel, channelErr := field[string](object, "channel", true)
	data, dataErr := field[string](object, "data", true)
	stamp, stampErr := integerField(object, "timestamp", true)
	if err = errors.Join(cursorErr, channelErr, dataErr, stampErr); err != nil {
		return LogFrame{}, err
	}
	return LogFrame{
		Cursor: cursor, Channel: processLogChannel(channel), RawChannel: channel, Data: data,
		Timestamp: time.UnixMilli(stamp).UTC(), Encoding: encoding, RawEncoding: string(encoding),
	}, nil
}

func processCollectLogs(ctx context.Context, handle *ProcessHandle, maxPages int) ([]LogFrame, error) {
	return handle.collectLogs(ctx, maxPages, nil)
}

func (h *ProcessHandle) collectLogs(ctx context.Context, maxPages int, initial *ProcessLogPage) ([]LogFrame, error) {
	logs := make([]LogFrame, 0)
	cursor := ""
	seen := make(map[string]bool)
	pages := 0
	if initial != nil {
		logs = append(logs, initial.Items...)
		if initial.EOF {
			return logs, nil
		}
		// The create snapshot predates waiting; even an empty snapshot can
		// have output after it by the time replay begins.
		cursor = initial.NextCursor
		seen[cursor] = true
		pages++
	}
	for ; maxPages == 0 || pages < maxPages; pages++ {
		page, err := h.LogPage(ctx, &ProcessLogsOptions{Cursor: cursor})
		if err != nil {
			return nil, err
		}
		logs = append(logs, page.Items...)
		if page.EOF || page.NextCursor == "" || len(page.Items) == 0 || seen[page.NextCursor] {
			return logs, nil
		}
		seen[page.NextCursor] = true
		cursor = page.NextCursor
	}
	return logs, nil
}
