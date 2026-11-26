// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

type execR3CanceledReader struct{ ctx context.Context }

func (r execR3CanceledReader) Read([]byte) (int, error) {
	<-r.ctx.Done()
	return 0, r.ctx.Err()
}

func TestExecR3_whenStreamTimesOut_mergesRetainedReplayByCursor(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given live output contains an evicted frame and a frame repeated in replay.
		mux := http.NewServeMux()
		mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
		page := processLogsFixture(
			map[string]any{"cursor": "2", "timestamp": 2, "channel": "stderr", "data": "shared"},
			map[string]any{"cursor": "3", "timestamp": 3, "channel": "stdout", "data": "replay"},
		)
		mux.HandleFunc("GET /processes/p/logs", jsonHandler(t, page))
		// Still running at the deadline: the result is timed_out.
		mux.HandleFunc("GET /processes/p", jsonHandler(t, processFixture("p")))
		snapshotReads := 0
		sandbox := dockerTimingSandbox(t, func(r *http.Request) (*http.Response, error) {
			if r.URL.Query().Get("follow") != "true" {
				if r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/logs") {
					snapshotReads++
				}
				return (waitTestTransport{handler: mux.ServeHTTP}).RoundTrip(r)
			}
			body := io.MultiReader(strings.NewReader(
				"event: log\nid: 1\ndata: {\"timestamp\":1,\"channel\":\"stdout\",\"data\":\"evicted\"}\n\n"+
					"event: log\nid: 2\ndata: {\"timestamp\":2,\"channel\":\"stderr\",\"data\":\"shared\"}\n\n"),
				execR3CanceledReader{ctx: r.Context()})
			return &http.Response{
				StatusCode: http.StatusOK, Header: http.Header{"Content-Type": {"text/event-stream"}},
				Body: io.NopCloser(body), Request: r,
			}, nil
		})
		var stdout, stderr bytes.Buffer
		// When the observation budget expires before EOF.
		result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{
			Timeout: time.Second, Stdout: &stdout, Stderr: &stderr,
		})
		// Then replay augments delivered output without losing or repeating frames.
		require.NoError(t, err)
		require.Equal(t, ProcessReasonTimedOut, result.Reason)
		require.Equal(t, "evictedsharedreplay", result.Output)
		require.Equal(t, "evictedreplay", result.Stdout)
		require.Equal(t, "shared", result.Stderr)
		require.Len(t, result.Logs, 3)
		require.Equal(t, result.Stdout, stdout.String())
		require.Equal(t, "shared", stderr.String())
		require.Equal(t, 1, snapshotReads)
	})
}
