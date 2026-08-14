// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"

	sdkerrors "github.com/daytona/clients/sdk-go/pkg/errors"
	toolbox "github.com/daytona/clients/toolbox-api-client-go"
)

// LogEvent is one replay or live event from [ProcessHandle.StreamLogs]. Type is
// "log", "state", "warning", "eof", or "error"; Cursor can resume a later stream.
type LogEvent struct {
	Type                 string                   // Event type.
	Cursor               string                   // Resume cursor after this event.
	Frame                *toolbox.ProcessLogFrame // Log payload for "log" events.
	Process              *toolbox.Process         // Process payload for "state" events.
	Message              string                   // Message for "warning" events.
	FirstAvailableCursor string                   // Recovery cursor for CURSOR_EXPIRED warnings.
	Err                  error                    // Decode or connection error for "error" events.
}

// StreamLogs replays retained logs from cursor, then follows live output until
// EOF or context cancellation. On CURSOR_EXPIRED, reconnect from the reported
// first available cursor. Use [ProcessHandle.Logs] for bounded page-by-page replay.
func (h *ProcessHandle) StreamLogs(ctx context.Context, cursor string) (<-chan LogEvent, error) {
	return h.streamLogs(ctx, cursor, "base64")
}

func (h *ProcessHandle) streamLogs(ctx context.Context, cursor, encoding string) (<-chan LogEvent, error) {
	baseURL := strings.TrimRight(h.service.toolboxClient.GetConfig().Servers[0].URL, "/")
	query := url.Values{"follow": []string{"true"}, "encoding": []string{encoding}}
	if cursor != "" {
		query.Set("cursor", cursor)
	}
	endpoint := fmt.Sprintf("%s/processes/%s/logs?%s", baseURL, url.PathEscape(h.processID), query.Encode())
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, sdkerrors.NewDaytonaConnectionError(fmt.Sprintf("create process log stream request: %v", err))
	}
	request.Header.Set("Accept", "text/event-stream")
	for key, value := range h.service.toolboxClient.GetConfig().DefaultHeader {
		request.Header.Set(key, value)
	}
	client := h.service.toolboxClient.GetConfig().HTTPClient
	if client == nil {
		client = http.DefaultClient
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, sdkerrors.NewDaytonaConnectionError(fmt.Sprintf("open process log stream: %v", err))
	}
	if response.StatusCode >= http.StatusMultipleChoices {
		defer response.Body.Close()
		body, readErr := io.ReadAll(response.Body)
		if readErr != nil {
			return nil, sdkerrors.NewDaytonaError(response.Status, response.StatusCode, response.Header)
		}
		return nil, sdkerrors.NewDaytonaErrorFromBody(body, response.StatusCode, response.Header)
	}

	events := make(chan LogEvent, 32)
	go readProcessLogStream(ctx, response.Body, events)
	return events, nil
}

func readProcessLogStream(ctx context.Context, body io.ReadCloser, events chan<- LogEvent) {
	defer close(events)
	defer body.Close()
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 64*1024), 1024*1024)
	var eventName string
	var data strings.Builder
	dispatch := func() bool {
		if data.Len() == 0 {
			eventName = ""
			return true
		}
		event, err := parseProcessLogEvent(eventName, data.String())
		eventName = ""
		data.Reset()
		if err != nil {
			event = LogEvent{Type: "error", Err: err}
		}
		select {
		case events <- event:
			return event.Type != "eof" && event.Type != "error"
		case <-ctx.Done():
			return false
		}
	}
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if !dispatch() {
				return
			}
			continue
		}
		if strings.HasPrefix(line, "event:") {
			eventName = strings.TrimSpace(strings.TrimPrefix(line, "event:"))
			continue
		}
		if strings.HasPrefix(line, "data:") {
			if data.Len() > 0 {
				data.WriteByte('\n')
			}
			data.WriteString(strings.TrimSpace(strings.TrimPrefix(line, "data:")))
		}
	}
	if data.Len() > 0 && !dispatch() {
		return
	}
	if err := scanner.Err(); err != nil && ctx.Err() == nil {
		events <- LogEvent{Type: "error", Err: sdkerrors.NewDaytonaConnectionError(fmt.Sprintf("read process log stream: %v", err))}
	}
}

func parseProcessLogEvent(eventName, data string) (LogEvent, error) {
	var marker struct {
		Channel              *toolbox.ProcessLogChannel `json:"channel"`
		State                *toolbox.ProcessState      `json:"state"`
		Cursor               string                     `json:"cursor"`
		Message              string                     `json:"message"`
		FirstAvailableCursor string                     `json:"firstAvailableCursor"`
	}
	if err := json.Unmarshal([]byte(data), &marker); err != nil {
		return LogEvent{}, fmt.Errorf("decode process log event: %w", err)
	}
	switch {
	case eventName == "log" || marker.Channel != nil:
		var frame toolbox.ProcessLogFrame
		if err := json.Unmarshal([]byte(data), &frame); err != nil {
			return LogEvent{}, fmt.Errorf("decode process log frame: %w", err)
		}
		return LogEvent{Type: "log", Cursor: frame.Cursor, Frame: &frame}, nil
	case eventName == "state" || marker.State != nil:
		var process toolbox.Process
		if err := json.Unmarshal([]byte(data), &process); err != nil {
			return LogEvent{}, fmt.Errorf("decode process state event: %w", err)
		}
		return LogEvent{Type: "state", Cursor: marker.Cursor, Process: &process}, nil
	case eventName == "warning" || marker.Message != "":
		return LogEvent{Type: "warning", Cursor: marker.Cursor, Message: marker.Message, FirstAvailableCursor: marker.FirstAvailableCursor}, nil
	case eventName == "eof" || marker.Cursor != "":
		return LogEvent{Type: "eof", Cursor: marker.Cursor}, nil
	default:
		return LogEvent{}, fmt.Errorf("unknown process log event %q", eventName)
	}
}
