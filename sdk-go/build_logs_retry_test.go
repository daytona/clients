// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestBuildLogsReconnectResumesAcknowledgedCursor(t *testing.T) {
	// Given a complete chunk, unknown checkpoint, and unacknowledged trailing frame.
	var attempts atomic.Int32
	first := buildLogsChunk("a", 0) + "event: future\nid: future-cursor\ndata: broken json\n\n" +
		"event: log\nid: partial\ndata: {"
	fake := buildLogsFake(t, func(w http.ResponseWriter, r *http.Request) {
		if attempts.Add(1) == 1 {
			buildLogsSSE(t, first)(w, r)
		} else {
			buildLogsSSE(t, buildLogsChunk("b", 1)+buildLogsDone("checkpoint/2+="))(w, r)
		}
	})
	client := newTestClient(t, fake.Server)
	// When following through the real public iterator.
	events, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image", nil))
	// Then discovery stays stable and reconnection resumes only delivered bytes.
	require.NoError(t, err)
	require.Len(t, events, 3)
	require.Equal(t, []byte("a"), buildLogsAs[BuildLogChunkEvent](t, events[0]).Data)
	require.Equal(t, []byte("b"), buildLogsAs[BuildLogChunkEvent](t, events[1]).Data)
	requests := fake.requests()
	require.Len(t, requests, 3)
	require.Equal(t, requests[1].Path, requests[2].Path)
	require.Equal(t, "checkpoint/1+=", requests[2].Query.Get("cursor"))
}

func TestBuildLogsRetryBudgetTracksProgress(t *testing.T) {
	tests := []struct {
		name     string
		bodies   []string
		wantCode string
		pauses   int
	}{
		{"EOF exhaustion", make([]string, 6), CodeBuildLogInterrupted, 5},
		{"state is not progress", []string{
			"event: state\ndata: {\"status\":\"building\"}\n\n",
			"event: state\ndata: {\"status\":\"building\"}\n\n", "", "", "", "",
		}, CodeBuildLogInterrupted, 5},
		{"cursor resets budget", []string{
			"", "", "", "", "", buildLogsChunk("a", 0),
			"", "", "", "", buildLogsDone("checkpoint/1+="),
		}, "", 10},
		{"server retry hint", []string{
			"event: error\ndata: {\"error\":\"retry\",\"code\":\"FUTURE\",\"retryable\":true}\n\n",
			buildLogsDone("empty"),
		}, "", 1},
		{
			"terminal server error",
			[]string{buildLogsChunk("a", 0) +
				"event: error\ndata: {\"error\":\"refused\",\"code\":\"FUTURE\",\"retryable\":false}\n\n"},
			"FUTURE", 0,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Given deterministic reconnect scheduling and real HTTP replies.
			var attempts atomic.Int32
			fake := buildLogsFake(t, func(w http.ResponseWriter, r *http.Request) {
				index := int(attempts.Add(1)) - 1
				if index >= len(tt.bodies) {
					t.Error("unexpected retry")
					w.WriteHeader(http.StatusForbidden)
					return
				}
				buildLogsSSE(t, tt.bodies[index])(w, r)
			})
			client := newTestClient(t, fake.Server)
			var delays []time.Duration
			follower := buildLogFollower{
				api: client.api(), path: "/images/image/build-logs-url", now: time.Now,
				state:  buildLogObservation{offsetKnown: true},
				jitter: func() float64 { return 1 },
				pause: func(_ context.Context, delay time.Duration) error {
					delays = append(delays, delay)
					return nil
				},
			}
			// When observing until done or exhausted.
			err := follower.follow(t.Context(), func(BuildLogEvent, error) bool { return true })
			// Then only progress renews the bounded retry budget.
			if tt.wantCode == "" {
				require.NoError(t, err)
			} else {
				var interrupted *BuildLogInterruptedError
				require.ErrorAs(t, err, &interrupted)
				require.Equal(t, tt.wantCode, interrupted.SDKError.Code)
				if tt.wantCode == "FUTURE" {
					require.Equal(t, "checkpoint/1+=", interrupted.Cursor)
				}
			}
			require.Len(t, delays, tt.pauses)
			require.Len(t, fake.requests(), len(tt.bodies)+1)
			if tt.name == "EOF exhaustion" {
				require.Equal(t, []time.Duration{
					250 * time.Millisecond, 500 * time.Millisecond,
					time.Second, 2 * time.Second, 5 * time.Second,
				}, delays)
			}
		})
	}
}

func TestBuildLogsHTTPRetryAfter(t *testing.T) {
	for _, status := range []int{429, 503} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			// Given an HTTP retry hint longer than the SDK backoff.
			var attempts atomic.Int32
			fake := buildLogsFake(t, func(w http.ResponseWriter, r *http.Request) {
				if attempts.Add(1) == 1 {
					w.Header().Set("Retry-After", "3")
					w.WriteHeader(status)
					return
				}
				buildLogsSSE(t, buildLogsDone("empty"))(w, r)
			})
			client := newTestClient(t, fake.Server)
			var delays []time.Duration
			follower := buildLogFollower{
				api: client.api(), path: "/images/image/build-logs-url", now: time.Now,
				jitter: func() float64 { return 1 },
				pause: func(_ context.Context, delay time.Duration) error {
					delays = append(delays, delay)
					return nil
				},
			}
			// When following using an injected retry scheduler.
			err := follower.follow(t.Context(), func(BuildLogEvent, error) bool { return true })
			// Then the server's lower bound wins.
			require.NoError(t, err)
			require.Equal(t, []time.Duration{3 * time.Second}, delays)
			require.Len(t, fake.requests(), 3)
		})
	}
}
