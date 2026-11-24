// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

func interpreterSSE(t *testing.T, w http.ResponseWriter, body string) {
	t.Helper()
	w.Header().Set("Content-Type", "text/event-stream")
	if _, err := io.WriteString(w, body); err != nil {
		t.Errorf("write interpreter stream: %v", err)
	}
}

func TestRunCode_whenStreamingDeliversBeforeCompletion(t *testing.T) {
	// Given
	delivered := make(chan struct{})
	var completed atomic.Bool
	routes := interpreterRunRoutes(t)
	routes["GET /processes/p/wait"] = func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-delivered:
			completed.Store(true)
			writeJSON(t, w, map[string]any{"reason": "exited", "exitCode": 1})
		case <-r.Context().Done():
		}
	}
	routes["GET /processes/p/logs"] = func(w http.ResponseWriter, _ *http.Request) {
		interpreterSSE(t, w, "event: log\nid: 1\ndata: "+
			`{"timestamp":1,"channel":"stdout","data":"hello"}`+"\n\n"+
			"event: log\nid: 2\ndata: "+`{"timestamp":2,"channel":"stderr","data":"warn"}`+"\n\n"+
			"event: log\nid: 3\ndata: "+`{"timestamp":3,"channel":"error","data":"{\"ename\":\"E\",\"traceback\":[\"line\"]}"}`+"\n\n"+
			"event: log\nid: 4\ndata: "+`{"timestamp":4,"channel":"execute_result","data":"{\"text/plain\":\"42\"}"}`+"\n\n"+
			"event: eof\nid: 4\ndata: {}\n\n")
	}
	fake := newFakeServer(t, routes)
	sandbox := interpreterTestSandbox(t, fake)
	var stdout, stderr bytes.Buffer
	var outputs []InterpreterOutput
	var exceptions []InterpreterError
	var frames []LogFrame
	var earlyDelivery bool
	// When
	result, err := sandbox.RunCode(t.Context(), "1", &RunCodeOptions{
		Stdout: &stdout, Stderr: &stderr,
		OnOutput: func(output InterpreterOutput) error {
			earlyDelivery = !completed.Load()
			outputs = append(outputs, output)
			output.Data["text/plain"] = []byte(`"changed"`)
			close(delivered)
			return nil
		},
		OnError: func(exception InterpreterError) error {
			exceptions = append(exceptions, exception)
			exception.Traceback[0] = "changed"
			return nil
		},
		OnLog: func(frame LogFrame) error { frames = append(frames, frame); return nil },
	})
	// Then
	require.NoError(t, err)
	require.True(t, earlyDelivery)
	require.Equal(t, "hello", stdout.String())
	require.Equal(t, "warn", stderr.String())
	require.Equal(t, InterpreterLogs{Stdout: "hello", Stderr: "warn"}, result.Logs)
	require.Len(t, outputs, 1)
	require.Len(t, exceptions, 1)
	require.Len(t, frames, 4)
	require.Equal(t, []string{"line"}, result.Error.Traceback)
	require.JSONEq(t, `"42"`, string(result.Results[0].Data["text/plain"]))
}

type interpreterFailWriter struct{ failure error }

func (w interpreterFailWriter) Write([]byte) (int, error) { return 0, w.failure }

func TestRunCode_whenDeliveryFailsCancelsWait(t *testing.T) {
	failure := errors.New("delivery failed")
	tests := []struct {
		name    string
		channel string
		opts    RunCodeOptions
	}{
		{"stdout", "stdout", RunCodeOptions{Stdout: interpreterFailWriter{failure}}},
		{"stderr", "stderr", RunCodeOptions{Stderr: interpreterFailWriter{failure}}},
		{"output", "display", RunCodeOptions{OnOutput: func(InterpreterOutput) error { return failure }}},
		{"error", "error", RunCodeOptions{OnError: func(InterpreterError) error { return failure }}},
		{"log", "stdout", RunCodeOptions{OnLog: func(LogFrame) error { return failure }}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given
			waitStarted, waitFinished := make(chan struct{}), make(chan struct{})
			routes := interpreterRunRoutes(t)
			routes["GET /processes/p/wait"] = func(_ http.ResponseWriter, r *http.Request) {
				close(waitStarted)
				<-r.Context().Done()
				close(waitFinished)
			}
			routes["GET /processes/p/logs"] = func(w http.ResponseWriter, _ *http.Request) {
				<-waitStarted
				interpreterSSE(t, w, fmt.Sprintf(
					"event: log\nid: 1\ndata: {\"timestamp\":1,\"channel\":%q,\"data\":\"{}\"}\n\n",
					test.channel))
			}
			fake := newFakeServer(t, routes)
			sandbox := interpreterTestSandbox(t, fake)
			// When
			_, err := sandbox.RunCode(t.Context(), "1", &test.opts)
			// Then
			require.ErrorIs(t, err, failure)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			<-waitFinished
		})
	}
}

func TestRunCode_whenStreamEndsWithoutEOF(t *testing.T) {
	// Given
	routes := interpreterRunRoutes(t)
	routes["GET /processes/p/logs"] = func(w http.ResponseWriter, _ *http.Request) {
		interpreterSSE(t, w, "event: log\nid: 1\ndata: "+
			`{"timestamp":1,"channel":"stdout","data":"partial"}`+"\n\n")
	}
	fake := newFakeServer(t, routes)
	sandbox := interpreterTestSandbox(t, fake)
	// When
	_, err := sandbox.RunCode(t.Context(), "1", &RunCodeOptions{Stdout: io.Discard})
	// Then
	require.ErrorIs(t, err, ErrConnection)
}
