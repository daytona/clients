// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func processSSEHandler(t *testing.T, body string) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		if _, err := w.Write([]byte(body)); err != nil {
			t.Error(err)
		}
	}
}

func TestStreamLogsDeliversVariantsWhenFramingUsesCRLF(t *testing.T) {
	// Given a complete stream with an unknown event whose payload is not JSON.
	state := processFixture("p")
	state["state"], state["reason"], state["cursor"] = "terminal", "exited", "2"
	stateJSON, err := json.Marshal(state)
	require.NoError(t, err)
	body := "\ufeff: heartbeat\r\n\r\nevent: future\r\ndata: not json\r\n\r\n" +
		"event: log\r\nid: 1\r\ndata: {\"channel\":\"stdout\",\"data\":\"ok\",\"timestamp\":1}\r\n\r\n" +
		"event: state\r\ndata: " + string(stateJSON) + "\r\n\r\n" +
		"event: warning\r\ndata: {\"cursor\":\"3\",\"message\":\"evicted\",\"firstAvailableCursor\":\"1\"}\r\n\r\n" +
		"event: eof\r\ndata: {\"cursor\":\"4\"}\r\n\r\n" +
		"event: error\r\ndata: {\"message\":\"must not be read\"}\r\n\r\n"
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{"GET /processes/p/logs": processSSEHandler(t, body)})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	// When following from a cursor.
	var events []ProcessLogEvent
	for event, streamErr := range handle.StreamLogs(t.Context(), &ProcessLogsOptions{Cursor: "resume", Limit: 5}) {
		require.NoError(t, streamErr)
		events = append(events, event)
	}
	// Then known events arrive in order, SSE ids supply missing cursors, and EOF terminates.
	require.Len(t, events, 4)
	frame, ok := events[0].(ProcessLogFrameEvent)
	require.True(t, ok)
	require.Equal(t, "1", frame.Cursor)
	require.Equal(t, "ok", frame.Frame.Data)
	require.IsType(t, ProcessLogStateEvent{}, events[1])
	require.IsType(t, ProcessLogWarningEvent{}, events[2])
	require.IsType(t, ProcessLogEOFEvent{}, events[3])
	request := fake.requests()[0]
	require.Equal(t, "resume", request.Header.Get("Last-Event-ID"))
	require.Equal(t, "true", request.Query.Get("follow"))
}

func TestStreamLogsReportsErrorWhenStreamIsIncompleteOrMalformed(t *testing.T) {
	for _, test := range []struct {
		name, body string
		want       error
	}{
		{"no eof", "event: log\ndata: {\"cursor\":\"1\",\"channel\":\"stdout\",\"data\":\"x\",\"timestamp\":1}\n\n", ErrConnection},
		{"unterminated eof", "event: eof\ndata: {\"cursor\":\"1\"}\n", ErrConnection},
		{"no data", "event: eof\n\n", ErrConnection},
		{"null cursor", "event: eof\nid: 1\ndata: {\"cursor\":null}\n\n", ErrBadRequest},
		{"missing cursor", "event: eof\ndata: {}\n\n", ErrBadRequest},
		{"malformed state", "event: state\ndata: {\"cursor\":\"1\",\"reason\":\"exited\"}\n\n", ErrBadRequest},
		{"invalid json", "event: log\ndata: not-json\n\n", ErrBadRequest},
		{"missing channel", "event: log\ndata: {\"cursor\":\"1\",\"data\":\"x\",\"timestamp\":1}\n\n", ErrBadRequest},
		{"daemon error", "event: error\ndata: {\"message\":\"gone\",\"code\":\"PROCESS_NOT_FOUND\"}\n\n", ErrProcessNotFound},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given the stream ends incorrectly or sends invalid known data.
			sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{"GET /processes/p/logs": processSSEHandler(t, test.body)})
			handle, err := sandbox.Process().Connect("p")
			require.NoError(t, err)
			// When consuming the stream.
			var failure error
			for _, streamErr := range handle.StreamLogs(t.Context(), nil) {
				if streamErr != nil {
					failure = streamErr
				}
			}
			// Then the incomplete log is never reported as complete.
			require.ErrorIs(t, failure, test.want)
		})
	}
}

func TestStreamLogsBoundsPartialEventWhenPeerNeverTerminatesIt(t *testing.T) {
	// Given a partial line larger than the configured event budget.
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p/logs": processSSEHandler(t, "event: future\ndata: "+strings.Repeat("x", 257)),
	})
	sandbox.client.config.maxResponseBytes = 128
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	// When reading a never-terminated event (unknown events count too).
	var failure error
	for _, streamErr := range handle.StreamLogs(t.Context(), nil) {
		failure = streamErr
	}
	// Then the cap applies as bytes arrive, not after a newline.
	require.ErrorIs(t, failure, ErrPayloadTooLarge)
}

func TestStreamBreakClosesOnlyItsResponseWhenAnotherCallUsesClient(t *testing.T) {
	// Given one open response and a separate ordinary route.
	closed := make(chan struct{})
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p/logs": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "text/event-stream")
			if _, err := w.Write([]byte("event: log\ndata: {\"cursor\":\"1\",\"channel\":\"stdout\",\"data\":\"x\",\"timestamp\":1}\n\n")); err != nil {
				t.Error(err)
				return
			}
			if err := http.NewResponseController(w).Flush(); err != nil {
				t.Error(err)
				return
			}
			<-r.Context().Done()
			close(closed)
		},
		"GET /processes/p": jsonHandler(t, processFixture("p")),
	})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	// When the consumer stops early.
	for _, streamErr := range handle.Logs(t.Context(), nil) {
		require.NoError(t, streamErr)
		break
	}
	<-closed
	// Then the client and unrelated requests remain usable.
	info, err := handle.Get(t.Context())
	require.NoError(t, err)
	require.Equal(t, "p", info.ID)
}

func TestStreamStopsWhenClientCloses(t *testing.T) {
	// Given a stream blocked waiting for another event.
	opened := make(chan struct{})
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p/logs": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "text/event-stream")
			if err := http.NewResponseController(w).Flush(); err != nil {
				t.Error(err)
				return
			}
			close(opened)
			<-r.Context().Done()
		},
	})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	done := make(chan error, 1)
	ctx := t.Context()
	go func() {
		for _, streamErr := range handle.StreamLogs(ctx, nil) {
			if streamErr != nil {
				done <- streamErr
				return
			}
		}
		done <- nil
	}()
	<-opened
	// When closing the owning client.
	require.NoError(t, sandbox.client.Close())
	// Then the blocked read is interrupted with the shared closed-client error.
	require.ErrorIs(t, <-done, ErrClientClosed)
}

func TestStreamCancellationPreservesCauseWhenAlreadyCancelled(t *testing.T) {
	// Given a canceled caller.
	sandbox, _ := processTestSandbox(t, nil)
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	// When attempting a follow.
	var failure error
	for _, streamErr := range handle.StreamLogs(ctx, nil) {
		failure = streamErr
	}
	// Then cancellation is reachable through the SDK error.
	require.ErrorIs(t, failure, context.Canceled)
}
