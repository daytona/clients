// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"io"
	"net/http"
	"strings"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

type execDelayedCloseBody struct {
	io.Reader
	closed <-chan time.Time
}

func (b execDelayedCloseBody) Close() error {
	<-b.closed
	return nil
}

func TestExecStreaming_whenFailurePrecedesDeadline_preservesStreamError(t *testing.T) {
	for _, tc := range []struct {
		name, tail string
		want       error
	}{
		{"missing eof", "", ErrConnection},
		{"malformed event", "event: log\ndata: not-json\n\n", ErrBadRequest},
	} {
		t.Run(tc.name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given a stream that fails immediately but finishes closing after the deadline.
				mux := http.NewServeMux()
				mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
				mux.HandleFunc("GET /processes/p/logs", jsonHandler(t, processLogsFixture()))
				snapshotReads := 0
				sandbox := dockerTimingSandbox(t, func(r *http.Request) (*http.Response, error) {
					if r.URL.Query().Get("follow") != "true" {
						if r.Method == http.MethodGet {
							snapshotReads++
						}
						return (waitTestTransport{handler: mux.ServeHTTP}).RoundTrip(r)
					}
					body := execDelayedCloseBody{
						Reader: strings.NewReader("event: log\nid: 1\ndata: " +
							"{\"channel\":\"stdout\",\"data\":\"first\",\"timestamp\":1}\n\n" + tc.tail),
						closed: time.After(2 * time.Second),
					}
					return &http.Response{
						StatusCode: http.StatusOK, Header: http.Header{"Content-Type": {"text/event-stream"}},
						Body: body, Request: r,
					}, nil
				})
				var stdout bytes.Buffer
				// When the stream's teardown outlasts the observation budget.
				result, err := sandbox.Exec(t.Context(), "command", &ExecOptions{Timeout: time.Second, Stdout: &stdout})
				// Then the original failure and partial result survive, without timeout replay.
				require.ErrorIs(t, err, tc.want)
				require.Equal(t, ProcessReasonUnknown, result.Reason)
				require.Equal(t, "p", result.ProcessID)
				require.Equal(t, "first", result.Stdout)
				require.Equal(t, result.Stdout, stdout.String())
				require.Zero(t, snapshotReads)
			})
		})
	}
}
