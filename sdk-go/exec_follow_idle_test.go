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

func TestIdleWatch_whenBytesArriveWithinTheWindow_neverExpires(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a watch kicked just before every window ends.
		expired := make(chan struct{})
		watch := newIdleWatch(time.Second, func() { close(expired) })
		defer watch.stop()
		for range 5 {
			time.Sleep(999 * time.Millisecond)
			watch.kick()
		}
		synctest.Wait()
		select {
		case <-expired:
			t.Fatal("expired while bytes kept arriving")
		default:
		}
		// When nothing arrives for a whole window.
		time.Sleep(time.Second)
		synctest.Wait()
		// Then it expires exactly once.
		select {
		case <-expired:
		default:
			t.Fatal("did not expire after an idle window")
		}
	})
}

func TestExec_whenTheDeadlineProcessReadStalls_reportsTimedOutWithinItsBound(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a run whose stream and post-deadline process read both stall.
		mux := http.NewServeMux()
		mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
		mux.HandleFunc("GET /processes/p/logs", jsonHandler(t, processLogsFixture()))
		mux.HandleFunc("GET /processes/p", func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() })
		sandbox, _ := execDroppedStreamSandbox(t, mux,
			"event: log\nid: 1\ndata: {\"timestamp\":1,\"channel\":\"stdout\",\"data\":\"out\"}\n\n")
		started := time.Now()
		// When the run's budget expires.
		result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{Timeout: time.Second, Stdout: io.Discard})
		// Then the read is cut at its bound, not the request timeout, and the run times out.
		require.NoError(t, err)
		require.Equal(t, ProcessReasonTimedOut, result.Reason)
		require.Equal(t, time.Second+execDeadlineReadBound, time.Since(started))
	})
}

func TestIdleWatch_whenStopped_neverExpires(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		expired := false
		watch := newIdleWatch(time.Second, func() { expired = true })
		watch.stop()
		time.Sleep(3 * time.Second)
		synctest.Wait()
		require.False(t, expired)
	})
}

func TestExec_whenAFollowRequestNeverAnswers_resumesAfterTheIdleBound(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a first follow request whose response headers never arrive.
		mux := http.NewServeMux()
		mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
		mux.HandleFunc("GET /processes/p/wait", jsonHandler(t, map[string]any{"reason": "exited", "exitCode": 0}))
		follows := 0
		sandbox := dockerTimingSandbox(t, func(r *http.Request) (*http.Response, error) {
			if r.URL.Query().Get("follow") != "true" {
				return (waitTestTransport{handler: mux.ServeHTTP}).RoundTrip(r)
			}
			follows++
			if follows == 1 {
				<-r.Context().Done()
				return nil, r.Context().Err()
			}
			return &http.Response{
				StatusCode: http.StatusOK, Header: http.Header{"Content-Type": {"text/event-stream"}},
				Body: io.NopCloser(strings.NewReader(
					"event: log\nid: 1\ndata: {\"timestamp\":1,\"channel\":\"stdout\",\"data\":\"ok\"}\n\nevent: eof\nid: 2\ndata: {}\n\n")),
				Request: r,
			}, nil
		})
		started := time.Now()
		// When running with no timeout and an output writer.
		result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{Timeout: WaitForever, Stdout: io.Discard})
		// Then the idle bound, not the request timeout, ends the first request.
		require.NoError(t, err)
		require.Equal(t, ProcessReasonExited, result.Reason)
		require.Equal(t, "ok", result.Stdout)
		require.Equal(t, 2, follows)
		require.Equal(t, execFollowIdle, time.Since(started))
	})
}

func TestExec_whenTheCallerSpendsLongOnAnEvent_doesNotResume(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a healthy stream whose second frame arrives a second after the first.
		mux := http.NewServeMux()
		mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
		mux.HandleFunc("GET /processes/p/wait", jsonHandler(t, map[string]any{"reason": "exited", "exitCode": 0}))
		follows := 0
		sandbox := dockerTimingSandbox(t, func(r *http.Request) (*http.Response, error) {
			if r.URL.Query().Get("follow") != "true" {
				return (waitTestTransport{handler: mux.ServeHTTP}).RoundTrip(r)
			}
			follows++
			parts := []string{
				"event: log\nid: 1\ndata: {\"timestamp\":1,\"channel\":\"stdout\",\"data\":\"a\"}\n\n",
				"event: log\nid: 2\ndata: {\"timestamp\":2,\"channel\":\"stdout\",\"data\":\"b\"}\n\nevent: eof\nid: 3\ndata: {}\n\n",
			}
			if r.Header.Get("Last-Event-ID") == "1" {
				parts = parts[1:]
			}
			return &http.Response{
				StatusCode: http.StatusOK, Header: http.Header{"Content-Type": {"text/event-stream"}},
				Body: io.NopCloser(&execLaterFrames{ctx: r.Context(), parts: parts}), Request: r,
			}, nil
		})
		// When the caller's writer takes longer than the idle bound on the first frame.
		result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{
			Timeout: WaitForever, Stdout: execSlowWriter{delay: 2 * execFollowIdle},
		})
		// Then the stream is not treated as dropped: one follow request, no resume.
		require.NoError(t, err)
		require.Equal(t, "ab", result.Stdout)
		require.Equal(t, 1, follows)
	})
}

func TestExec_whenTheCallerCancelsDuringTheDeadlineProcessRead_reportsTheCancellation(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a run whose post-deadline process read sees the caller cancel.
		ctx, cancel := context.WithCancel(t.Context())
		defer cancel()
		mux := http.NewServeMux()
		mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
		mux.HandleFunc("GET /processes/p/logs", jsonHandler(t, processLogsFixture()))
		mux.HandleFunc("GET /processes/p", func(_ http.ResponseWriter, r *http.Request) {
			cancel()
			<-r.Context().Done()
		})
		sandbox, _ := execDroppedStreamSandbox(t, mux,
			"event: log\nid: 1\ndata: {\"timestamp\":1,\"channel\":\"stdout\",\"data\":\"out\"}\n\n")
		// When the run's budget expires.
		result, err := sandbox.Exec(ctx, "command", &ExecOptions{Timeout: time.Second, Stdout: io.Discard})
		// Then the cancellation is the answer, never a clean timed_out.
		require.ErrorIs(t, err, context.Canceled)
		require.Equal(t, ProcessReasonUnknown, result.Reason)
		require.Equal(t, "out", result.Stdout)
	})
}

// execLaterFrames returns its parts one per Read, a second apart, until its
// request is cancelled.
type execLaterFrames struct {
	ctx   context.Context
	parts []string
}

func (r *execLaterFrames) Read(p []byte) (int, error) {
	if len(r.parts) == 0 {
		return 0, io.EOF
	}
	select {
	case <-r.ctx.Done():
		return 0, r.ctx.Err()
	case <-time.After(time.Second):
	}
	n := copy(p, r.parts[0])
	r.parts[0] = r.parts[0][n:]
	if r.parts[0] == "" {
		r.parts = r.parts[1:]
	}
	return n, nil
}

type execSlowWriter struct{ delay time.Duration }

func (w execSlowWriter) Write(p []byte) (int, error) {
	time.Sleep(w.delay)
	return len(p), nil
}
