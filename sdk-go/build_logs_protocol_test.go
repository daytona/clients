// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestBuildLogsMalformedProtocolDoesNotRetry(t *testing.T) {
	tests := map[string]string{
		"wrong offset type": `event: log
id: next
data: {"data_base64":"YQ==","start_offset":0,"next_offset":"1"}

`,
		"noncanonical offset": `event: log
id: next
data: {"data_base64":"YQ==","start_offset":"00","next_offset":"1"}

`,
		"overflow": `event: log
id: next
data: {"data_base64":"YQ==","start_offset":"18446744073709551615","next_offset":"18446744073709551616"}

`,
		"wrong byte count": `event: log
id: next
data: {"data_base64":"YQ==","start_offset":"0","next_offset":"2"}

`,
		"empty chunk": `event: log
id: next
data: {"data_base64":"","start_offset":"0","next_offset":"0"}

`,
		"noncanonical base64": `event: log
id: next
data: {"data_base64":"YR==","start_offset":"0","next_offset":"1"}

`,
		"missing id": `event: log
data: {"data_base64":"YQ==","start_offset":"0","next_offset":"1"}

`,
		"repeated id":   buildLogsChunk("a", 0) + strings.ReplaceAll(buildLogsChunk("b", 1), "checkpoint/2+=", "checkpoint/1+="),
		"state id":      "event: state\nid: bad\ndata: {\"status\":\"building\"}\n\n",
		"state type":    "event: state\ndata: {\"status\":5}\n\n",
		"state null":    "event: state\ndata: {\"status\":null}\n\n",
		"deadline":      "event: state\ndata: {\"status\":\"building\",\"deadline_at\":\"2026-01-01\"}\n\n",
		"deadline type": "event: state\ndata: {\"status\":\"building\",\"deadline_at\":42}\n\n",
		"done type":     "event: done\ndata: {\"status\":\"ready\",\"reason\":\"terminal\",\"output_complete\":1}\n\n",
		"done advances": buildLogsChunk("a", 0) + buildLogsDone("other"),
		"done id mismatch": "event: done\nid: other\ndata: " +
			`{"status":"ready","reason":"terminal","output_complete":true,"cursor":"final"}` + "\n\n",
		"gap count": "event: gap\nid: gap\ndata: " +
			`{"reason":"truncated","requested_offset":"0","first_available_offset":"3","dropped_bytes":"2"}` + "\n\n",
		"gap reversed": "event: gap\nid: gap\ndata: " +
			`{"reason":"truncated","requested_offset":"3","first_available_offset":"0","dropped_bytes":"3"}` + "\n\n",
		"unavailable":   "event: unavailable\ndata: {}\n\n",
		"error type":    "event: error\ndata: {\"error\":\"broken\",\"code\":\"X\",\"retryable\":\"true\"}\n\n",
		"malformed":     "event: state\ndata: invalid\n\n",
		"invalid UTF8":  "event: future\ndata: \xff\n\n",
		"duplicate id":  "event: log\nid: a\nid: b\ndata: {}\n\n",
		"bare id":       "id\n\n",
		"id no data":    "id: orphan\n\n",
		"oversize tail": "data: " + strings.Repeat("x", buildLogMaxEvent),
		"oversize full": "data: " + strings.Repeat("x", buildLogMaxEvent) + "\n\n",
	}
	for name, body := range tests {
		t.Run(name, func(t *testing.T) {
			// Given a malformed response at the HTTP boundary.
			fake := buildLogsFake(t, buildLogsSSE(t, body))
			client := newTestClient(t, fake.Server)
			// When following the build.
			_, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image", nil))
			// Then the failure is typed and never retried.
			var interrupted *BuildLogInterruptedError
			require.ErrorAs(t, err, &interrupted)
			require.Equal(t, "BUILD_LOG_PROTOCOL_ERROR", interrupted.SDKError.Code)
			require.Len(t, fake.requests(), 2)
		})
	}
}

func TestBuildLogsCallerValidationPrecedesNetwork(t *testing.T) {
	tests := []struct {
		name string
		id   string
		opts FollowBuildLogsOptions
	}{
		{"empty id", "", FollowBuildLogsOptions{}},
		{"dot id", ".", FollowBuildLogsOptions{}},
		{"dotdot id", "..", FollowBuildLogsOptions{}},
		{"blank id", " ", FollowBuildLogsOptions{}},
		{"invalid utf8 id", "\xff", FollowBuildLogsOptions{}},
		{"control cursor", "image", FollowBuildLogsOptions{Cursor: "x\ty"}},
		{"DEL cursor", "image", FollowBuildLogsOptions{Cursor: "x\x7fy"}},
		{"large cursor", "image", FollowBuildLogsOptions{Cursor: strings.Repeat("x", 2049)}},
		{"invalid utf8 cursor", "image", FollowBuildLogsOptions{Cursor: "\xff"}},
		{"negative timeout", "image", FollowBuildLogsOptions{Timeout: -4}},
		{"NoWait timeout", "image", FollowBuildLogsOptions{Timeout: NoWait}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Given invalid caller options.
			fake := buildLogsFake(t, buildLogsSSE(t, ""))
			client := newTestClient(t, fake.Server)
			// When consuming the iterator.
			_, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), tt.id, &tt.opts))
			// Then local BAD_REQUEST is returned without discovery.
			require.ErrorIs(t, err, ErrBadRequest)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Equal(t, "SDK", sdkError.Source)
			require.Empty(t, fake.requests())
		})
	}
}

func TestBuildLogsSourceChangedRetainsCheckpoint(t *testing.T) {
	// Given a delivered chunk followed by replacement.
	fake := buildLogsFake(t, buildLogsSSE(t, buildLogsChunk("a", 0)+
		"event: source_changed\ndata: {\"reason\":\"replacement\"}\n\n"))
	client := newTestClient(t, fake.Server)
	// When following.
	events, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image", nil))
	// Then replacement is emitted and no zero restart is attempted.
	require.Len(t, events, 2)
	require.IsType(t, BuildLogSourceChangedEvent{}, events[1])
	var interrupted *BuildLogInterruptedError
	require.ErrorAs(t, err, &interrupted)
	require.Equal(t, "BUILD_LOG_SOURCE_CHANGED", interrupted.SDKError.Code)
	require.Equal(t, "checkpoint/1+=", interrupted.Cursor)
	require.Len(t, fake.requests(), 2)
}
