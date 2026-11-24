// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestRunCodeR3_whenCallbackCancels_suppressesBufferedFrames(t *testing.T) {
	// Given all frames are buffered before the first callback cancels.
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	mux := http.NewServeMux()
	mux.HandleFunc("POST /processes", jsonHandler(t, interpreterProcessFixture("p")))
	mux.HandleFunc("GET /processes/p/wait", func(_ http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	})
	mux.HandleFunc("GET /processes/p/logs", processSSEHandler(t,
		"event: log\nid: 1\ndata: {\"channel\":\"setup\",\"data\":\"setup\",\"timestamp\":1}\n\n"+
			"event: log\nid: 2\ndata: {\"channel\":\"stdout\",\"data\":\"out\",\"timestamp\":2}\n\n"+
			"event: log\nid: 3\ndata: {\"channel\":\"stderr\",\"data\":\"err\",\"timestamp\":3}\n\n"+
			"event: log\nid: 4\ndata: {\"channel\":\"display\",\"data\":\"{\\\"text/plain\\\":\\\"42\\\"}\",\"timestamp\":4}\n\n"+
			"event: log\nid: 5\ndata: {\"channel\":\"error\",\"data\":\"{\\\"ename\\\":\\\"E\\\",\\\"evalue\\\":\\\"bad\\\",\\\"traceback\\\":[]}\",\"timestamp\":5}\n\n"+
			"event: eof\nid: 5\ndata: {}\n\n"))
	sandbox := dockerTimingSandbox(t, waitTestTransport{handler: mux.ServeHTTP}.RoundTrip)
	var stdout, stderr bytes.Buffer
	var cursors []string
	outputs, exceptions := 0, 0
	// When the first callback cancels the caller context.
	_, err := sandbox.RunCode(ctx, "1", &RunCodeOptions{
		Stdout: &stdout, Stderr: &stderr,
		OnLog: func(frame LogFrame) error {
			cursors = append(cursors, frame.Cursor)
			cancel()
			return nil
		},
		OnOutput: func(InterpreterOutput) error { outputs++; return nil },
		OnError:  func(InterpreterError) error { exceptions++; return nil },
	})
	// Then no later buffered frame reaches a callback or writer.
	require.ErrorIs(t, err, context.Canceled)
	require.Equal(t, []string{"1"}, cursors)
	require.Empty(t, stdout.String())
	require.Empty(t, stderr.String())
	require.Zero(t, outputs)
	require.Zero(t, exceptions)
}
