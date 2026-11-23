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

func TestRunCode_whenTimedOutStreamUsesBoundedSnapshot(t *testing.T) {
	// Given
	delivered := make(chan struct{})
	routes := interpreterRunRoutes(t)
	routes["GET /processes/p/wait"] = func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-delivered:
			writeJSON(t, w, map[string]any{"reason": "timed_out", "exitCode": 124})
		case <-r.Context().Done():
		}
	}
	terminal := interpreterProcessFixture("p")
	terminal["state"], terminal["reason"], terminal["exitCode"] = "terminal", "timed_out", 124
	routes["GET /processes/p"] = jsonHandler(t, terminal)
	routes["GET /processes/p/logs"] = func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("follow") == "true" {
			interpreterSSE(t, w, "event: log\nid: 1\ndata: "+
				`{"timestamp":1,"channel":"stdout","data":"first"}`+"\n\n")
			if err := http.NewResponseController(w).Flush(); err != nil {
				t.Errorf("flush interpreter stream: %v", err)
			}
			<-r.Context().Done()
			return
		}
		writeJSON(t, w, map[string]any{
			"frames": []any{
				map[string]any{"cursor": "1", "timestamp": 1, "channel": "stdout", "data": "first"},
				map[string]any{"cursor": "2", "timestamp": 2, "channel": "stdout", "data": "second"},
			}, "nextCursor": "2", "eof": true, "truncatedHead": false,
		})
	}
	fake := newFakeServer(t, routes)
	sandbox := interpreterTestSandbox(t, fake)
	var stdout bytes.Buffer
	// When
	result, err := sandbox.RunCode(t.Context(), "1", &RunCodeOptions{
		Stdout: &stdout,
		OnLog: func(frame LogFrame) error {
			if frame.Cursor == "1" {
				close(delivered)
			}
			return nil
		},
	})
	// Then
	require.NoError(t, err)
	require.Equal(t, ProcessReasonTimedOut, result.Process.Reason)
	require.Equal(t, 124, *result.Process.ExitCode)
	require.Equal(t, "firstsecond", result.Logs.Stdout)
	require.Equal(t, "firstsecond", stdout.String())
}

func TestRunCode_whenStreamingCallerCancels(t *testing.T) {
	// Given
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	waitStarted := make(chan struct{})
	routes := interpreterRunRoutes(t)
	routes["GET /processes/p/wait"] = func(_ http.ResponseWriter, r *http.Request) {
		close(waitStarted)
		<-r.Context().Done()
	}
	routes["GET /processes/p/logs"] = func(w http.ResponseWriter, r *http.Request) {
		<-waitStarted
		interpreterSSE(t, w, "event: log\nid: 1\ndata: "+
			`{"timestamp":1,"channel":"stdout","data":"first"}`+"\n\n")
		if err := http.NewResponseController(w).Flush(); err != nil {
			t.Errorf("flush interpreter stream: %v", err)
		}
		<-r.Context().Done()
	}
	fake := newFakeServer(t, routes)
	sandbox := interpreterTestSandbox(t, fake)
	// When
	_, err := sandbox.RunCode(ctx, "1", &RunCodeOptions{
		OnLog: func(LogFrame) error { cancel(); return nil },
	})
	// Then
	require.ErrorIs(t, err, context.Canceled)
}
