// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/daytona/clients/sdk-go/pkg/options"
	"github.com/daytona/clients/sdk-go/pkg/types"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestParseProcessLogEvent_classification(t *testing.T) {
	// Given
	tests := []struct {
		name         string
		eventName    string
		data         string
		wantType     string
		wantCursor   string
		wantMessage  string
		wantErrorSub string
	}{
		{
			name:       "named log event",
			eventName:  "log",
			data:       `{"seq":1,"cursor":"c1","channel":"stdout","timestamp":"2026-08-14T00:00:00Z","data":"aGk=","encoding":"base64"}`,
			wantType:   "log",
			wantCursor: "c1",
		},
		{
			name:       "named state event",
			eventName:  "state",
			data:       `{"id":"p1","cursor":"c2","state":"exited","kind":"exec","createdAt":"2026-08-14T00:00:00Z"}`,
			wantType:   "state",
			wantCursor: "c2",
		},
		{
			name:        "named warning event carries recovery fields",
			eventName:   "warning",
			data:        `{"cursor":"c3","message":"frames evicted","firstAvailableCursor":"c3"}`,
			wantType:    "warning",
			wantCursor:  "c3",
			wantMessage: "frames evicted",
		},
		{
			name:       "named eof event",
			eventName:  "eof",
			data:       `{"cursor":"c4"}`,
			wantType:   "eof",
			wantCursor: "c4",
		},
		{
			name:         "unknown cursor-bearing event is an error, not eof",
			eventName:    "checkpoint",
			data:         `{"cursor":"c5"}`,
			wantErrorSub: `unknown process log event "checkpoint"`,
		},
		{
			name:         "unnamed cursor-only event is an error, not eof",
			eventName:    "",
			data:         `{"cursor":"c6"}`,
			wantErrorSub: `unknown process log event ""`,
		},
		{
			name:         "malformed payload is a decode error",
			eventName:    "log",
			data:         `{`,
			wantErrorSub: "decode process log event",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// When
			event, err := parseProcessLogEvent(test.eventName, test.data)

			// Then
			if test.wantErrorSub != "" {
				require.Error(t, err)
				assert.Contains(t, err.Error(), test.wantErrorSub)
				return
			}
			require.NoError(t, err)
			assert.Equal(t, test.wantType, event.Type)
			assert.Equal(t, test.wantCursor, event.Cursor)
			assert.Equal(t, test.wantMessage, event.Message)
		})
	}
}

func TestValidateTimeoutMs_rejects_out_of_range_values(t *testing.T) {
	// Given
	tests := []struct {
		name         string
		value        int
		wantErrorSub string
	}{
		{name: "zero is allowed", value: 0},
		{name: "in-range value is allowed", value: 5000},
		{name: "int32 maximum is allowed", value: math.MaxInt32},
		{name: "negative is rejected", value: -1, wantErrorSub: "timeoutMs must be non-negative"},
		{
			name:         "above int32 maximum is rejected",
			value:        math.MaxInt32 + 1,
			wantErrorSub: fmt.Sprintf("timeoutMs must not exceed %d", math.MaxInt32),
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// When
			err := validateTimeoutMs("timeoutMs", test.value)

			// Then
			if test.wantErrorSub == "" {
				require.NoError(t, err)
				return
			}
			require.Error(t, err)
			assert.Contains(t, err.Error(), test.wantErrorSub)
		})
	}
}

func TestBuildCreateProcessRequest_rejects_out_of_int32_range_timeout(t *testing.T) {
	// Given
	tests := []struct {
		name         string
		timeoutMs    int
		wantErrorSub string
	}{
		{name: "int32 maximum is accepted", timeoutMs: math.MaxInt32},
		{
			name:         "above int32 maximum is rejected before wrapping",
			timeoutMs:    math.MaxInt32 + 1,
			wantErrorSub: fmt.Sprintf("timeoutMs must not exceed %d", math.MaxInt32),
		},
		{name: "negative is rejected", timeoutMs: -1, wantErrorSub: "timeoutMs must be non-negative"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// When
			request, err := buildCreateProcessRequest(options.Apply(
				options.WithProcessArgv("echo", "hi"),
				options.WithProcessTimeout(test.timeoutMs),
			))

			// Then
			if test.wantErrorSub == "" {
				require.NoError(t, err)
				assert.Equal(t, int32(math.MaxInt32), request.GetTimeoutMs())
				return
			}
			require.Error(t, err)
			assert.Contains(t, err.Error(), test.wantErrorSub)
		})
	}
}

func TestProcessRun_rejects_out_of_int32_range_wait_timeout(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("unexpected request to %s", r.URL.Path)
	}))
	defer server.Close()
	service := NewProcessService(createTestToolboxClient(server), nil, types.CodeLanguagePython)

	// When
	_, err := service.Run(context.Background(),
		options.WithProcessArgv("echo", "hi"),
		options.WithProcessWaitTimeout(math.MaxInt32+1),
	)

	// Then
	require.Error(t, err)
	assert.Contains(t, err.Error(), fmt.Sprintf("waitTimeoutMs must not exceed %d", math.MaxInt32))
}

func TestProcessHandleResize_rejects_out_of_int32_range_dimensions(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("unexpected request to %s", r.URL.Path)
	}))
	defer server.Close()
	handle := newProcessHandle("process-1", NewProcessService(createTestToolboxClient(server), nil, types.CodeLanguagePython))
	tests := []struct {
		name         string
		cols, rows   int
		wantErrorSub string
	}{
		{name: "non-positive cols", cols: 0, rows: 24, wantErrorSub: "terminal dimensions must be positive"},
		{name: "non-positive rows", cols: 80, rows: -1, wantErrorSub: "terminal dimensions must be positive"},
		{
			name: "cols above int32 maximum", cols: math.MaxInt32 + 1, rows: 24,
			wantErrorSub: fmt.Sprintf("terminal dimensions must not exceed %d", math.MaxInt32),
		},
		{
			name: "rows above int32 maximum", cols: 80, rows: math.MaxInt32 + 1,
			wantErrorSub: fmt.Sprintf("terminal dimensions must not exceed %d", math.MaxInt32),
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// When
			err := handle.Resize(context.Background(), test.cols, test.rows)

			// Then
			require.Error(t, err)
			assert.Contains(t, err.Error(), test.wantErrorSub)
		})
	}
}

func TestProcessHandleWait_rejects_out_of_int32_range_timeout(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("unexpected request to %s", r.URL.Path)
	}))
	defer server.Close()
	handle := newProcessHandle("process-1", NewProcessService(createTestToolboxClient(server), nil, types.CodeLanguagePython))

	// When
	_, err := handle.Wait(context.Background(), math.MaxInt32+1)

	// Then
	require.Error(t, err)
	assert.Contains(t, err.Error(), fmt.Sprintf("timeoutMs must not exceed %d", math.MaxInt32))
}

func TestProcessHandleCollectOutput_errors_when_page_cap_is_exhausted(t *testing.T) {
	// Given a lowered page cap so exhaustion needs 25 round trips, not 10k
	originalCap := maxProcessLogPages
	maxProcessLogPages = 25
	t.Cleanup(func() { maxProcessLogPages = originalCap })
	pages := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		pages++
		writeJSONResponse(t, w, http.StatusOK, map[string]any{
			"frames": []map[string]any{{
				"seq": pages, "cursor": fmt.Sprintf("c%d", pages), "channel": "stdout",
				"timestamp": "2026-08-14T00:00:00Z",
				"data":      "eA==", "encoding": "base64",
			}},
			"nextCursor": fmt.Sprintf("c%d", pages),
			"eof":        false,
		})
	}))
	defer server.Close()
	handle := newProcessHandle("process-1", NewProcessService(createTestToolboxClient(server), nil, types.CodeLanguagePython))

	// When
	stdout, stderr, err := handle.collectOutput(context.Background())

	// Then
	require.Error(t, err)
	assert.Contains(t, err.Error(), fmt.Sprintf("process log replay exceeded %d pages", maxProcessLogPages))
	assert.Empty(t, stdout)
	assert.Empty(t, stderr)
	assert.Equal(t, maxProcessLogPages, pages)
}

func TestProcessHandleCollectOutput_returns_output_when_log_ends(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(t, w, http.StatusOK, map[string]any{
			"frames": []map[string]any{{
				"seq": 1, "cursor": "c1", "channel": "stdout",
				"timestamp": "2026-08-14T00:00:00Z",
				"data":      "aGk=", "encoding": "base64",
			}},
			"nextCursor": "c1",
			"eof":        true,
		})
	}))
	defer server.Close()
	handle := newProcessHandle("process-1", NewProcessService(createTestToolboxClient(server), nil, types.CodeLanguagePython))

	// When
	stdout, stderr, err := handle.collectOutput(context.Background())

	// Then
	require.NoError(t, err)
	assert.Equal(t, "hi", stdout)
	assert.Empty(t, stderr)
}

func TestReadProcessLogStream_reports_unknown_event_instead_of_ending_silently(t *testing.T) {
	// Given
	stream := strings.Join([]string{
		"event: log",
		`data: {"seq":1,"cursor":"c1","channel":"stdout","timestamp":"2026-08-14T00:00:00Z","data":"aGk=","encoding":"base64"}`,
		"",
		"event: checkpoint",
		`data: {"cursor":"c2"}`,
		"",
		"event: eof",
		`data: {"cursor":"c3"}`,
		"",
	}, "\n")
	events := make(chan LogEvent, 8)

	// When
	readProcessLogStream(context.Background(), io.NopCloser(strings.NewReader(stream)), events)
	collected := make([]LogEvent, 0, 8)
	for event := range events {
		collected = append(collected, event)
	}

	// Then
	require.Len(t, collected, 2)
	assert.Equal(t, "log", collected[0].Type)
	assert.Equal(t, "error", collected[1].Type)
	require.Error(t, collected[1].Err)
	assert.Contains(t, collected[1].Err.Error(), `unknown process log event "checkpoint"`)
}
