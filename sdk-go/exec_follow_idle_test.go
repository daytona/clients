// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"strings"
	"sync"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

// execDroppedStreamSandbox answers the run's follow requests with the given
// bodies; a body ends by blocking until the request is cancelled, like a
// connection a network path dropped without closing. It records each follow
// request's Last-Event-ID.
func execDroppedStreamSandbox(t *testing.T, mux *http.ServeMux, bodies ...string) (*Sandbox, func() []string) {
	t.Helper()
	var mu sync.Mutex
	var resumes []string
	sandbox := dockerTimingSandbox(t, func(r *http.Request) (*http.Response, error) {
		if r.URL.Query().Get("follow") != "true" {
			return (waitTestTransport{handler: mux.ServeHTTP}).RoundTrip(r)
		}
		mu.Lock()
		index := len(resumes)
		resumes = append(resumes, r.Header.Get("Last-Event-ID"))
		mu.Unlock()
		require.Less(t, index, len(bodies), "unexpected follow request")
		var body io.Reader = strings.NewReader(bodies[index])
		if !strings.Contains(bodies[index], "event: eof") {
			body = io.MultiReader(body, execR3CanceledReader{ctx: r.Context()})
		}
		return &http.Response{
			StatusCode: http.StatusOK, Header: http.Header{"Content-Type": {"text/event-stream"}},
			Body: io.NopCloser(body), Request: r,
		}, nil
	})
	return sandbox, func() []string {
		mu.Lock()
		defer mu.Unlock()
		return append([]string(nil), resumes...)
	}
}

func TestExec_whenOutputStreamIsSilentlyDropped_resumesFromTheLastCursor(t *testing.T) {
	for _, timeout := range []time.Duration{0, WaitForever, 10 * time.Minute} {
		t.Run(timeout.String(), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given a stream that goes quiet without closing after its first frame.
				mux := http.NewServeMux()
				mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
				mux.HandleFunc("GET /processes/p/wait", jsonHandler(t, map[string]any{"reason": "exited", "exitCode": 0}))
				sandbox, resumes := execDroppedStreamSandbox(t, mux,
					"event: log\nid: 1\ndata: {\"timestamp\":1,\"channel\":\"stdout\",\"data\":\"first\"}\n\n",
					"event: log\nid: 2\ndata: {\"timestamp\":2,\"channel\":\"stdout\",\"data\":\"second\"}\n\n"+
						"event: eof\nid: 3\ndata: {}\n\n")
				var stdout bytes.Buffer
				started := time.Now()
				// When running with an output writer.
				result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{Timeout: timeout, Stdout: &stdout})
				// Then the idle stream is resumed after the delivered frame, and the
				// run reports how the process ended.
				require.NoError(t, err)
				require.Equal(t, ProcessReasonExited, result.Reason)
				require.NotNil(t, result.ExitCode)
				require.Equal(t, "firstsecond", result.Stdout)
				require.Equal(t, "firstsecond", stdout.String())
				require.Equal(t, []string{"", "1"}, resumes())
				require.Equal(t, execFollowIdle, time.Since(started))
			})
		})
	}
}

func TestExec_whenTheDeadlineFindsTheProcessEnded_reportsItsResult(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a stream that delivers output, then nothing, while the process ends.
		mux := http.NewServeMux()
		mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
		mux.HandleFunc("GET /processes/p/logs", jsonHandler(t, processLogsFixture()))
		ended := processFixture("p")
		ended["state"], ended["reason"], ended["exitCode"] = "terminal", "exited", 3
		mux.HandleFunc("GET /processes/p", jsonHandler(t, ended))
		sandbox, _ := execDroppedStreamSandbox(t, mux,
			"event: log\nid: 1\ndata: {\"timestamp\":1,\"channel\":\"stdout\",\"data\":\"out\"}\n\n")
		var stdout bytes.Buffer
		// When the run's budget expires before the stream's idle bound.
		result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{Timeout: time.Second, Stdout: &stdout})
		// Then the result is the process's own, never timed_out.
		require.NoError(t, err)
		require.Equal(t, ProcessReasonExited, result.Reason)
		require.NotNil(t, result.ExitCode)
		require.Equal(t, 3, *result.ExitCode)
		require.Equal(t, "out", result.Stdout)
	})
}

func TestStreamLogs_hasNoIdleBound(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a quiet stream that never closes.
		mux := http.NewServeMux()
		sandbox, _ := execDroppedStreamSandbox(t, mux, "")
		handle, err := sandbox.Process().Connect("p")
		require.NoError(t, err)
		ctx, cancel := context.WithTimeout(t.Context(), 10*execFollowIdle)
		defer cancel()
		// When following through the public stream.
		for _, streamErr := range handle.StreamLogs(ctx, nil) {
			// Then only the caller's context ends it.
			require.ErrorIs(t, streamErr, context.DeadlineExceeded)
		}
		require.Equal(t, context.DeadlineExceeded, ctx.Err())
	})
}
