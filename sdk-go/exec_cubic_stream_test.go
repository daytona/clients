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

type execCubicCancelWriter struct {
	output bytes.Buffer
	cancel context.CancelFunc
}

func (w *execCubicCancelWriter) Write(data []byte) (int, error) {
	n, err := w.output.Write(data)
	w.cancel()
	return n, err
}

func TestExecStreaming_whenWriterCancels_suppressesBufferedOutput(t *testing.T) {
	// Given two frames are already buffered when the first write cancels.
	mux := http.NewServeMux()
	mux.HandleFunc("POST /processes", jsonHandler(t, processFixture("p")))
	mux.HandleFunc("GET /processes/p/logs", processSSEHandler(t,
		"event: log\ndata: {\"cursor\":\"1\",\"channel\":\"stdout\",\"data\":\"first\",\"timestamp\":1}\n\n"+
			"event: log\ndata: {\"cursor\":\"2\",\"channel\":\"stderr\",\"data\":\"second\",\"timestamp\":2}\n\n"+
			"event: eof\ndata: {\"cursor\":\"2\"}\n\n"))
	sandbox := dockerTimingSandbox(t, waitTestTransport{handler: mux.ServeHTTP}.RoundTrip)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	writer := &execCubicCancelWriter{cancel: cancel}
	// When a streaming caller cancels while handling the first output.
	_, err := sandbox.Exec(ctx, "command", &ExecOptions{Stdout: writer, Stderr: writer})
	// Then cancellation is reachable and no later buffered output is written.
	require.ErrorIs(t, err, context.Canceled)
	require.Equal(t, "first", writer.output.String())
}
