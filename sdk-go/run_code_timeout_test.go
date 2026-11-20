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

func TestRunCode_whenSubmissionSpendsBudget(t *testing.T) {
	// Given
	var submitted atomic.Bool
	routes := interpreterRunRoutes(t)
	routes["POST /processes"] = func(w http.ResponseWriter, _ *http.Request) {
		submitted.Store(true)
		writeJSON(t, w, interpreterProcessFixture("p"))
	}
	routes["POST /processes/p/signals"] = func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}
	routes["GET /processes/p/wait"] = jsonHandler(t, map[string]any{"reason": "exited", "exitCode": 130})
	fake := newFakeServer(t, routes)
	sandbox := interpreterTestSandbox(t, fake)
	now := time.Now()
	sandbox.Interpreter().now = func() time.Time {
		if submitted.Load() {
			return now.Add(2 * time.Second)
		}
		return now
	}
	// When
	result, err := sandbox.RunCode(t.Context(), "loop()", &RunCodeOptions{Timeout: time.Second})
	// Then
	require.NoError(t, err)
	require.Equal(t, 130, *result.Process.ExitCode)
	requests := fake.requests()
	require.Len(t, requests, 4)
	require.Equal(t, "/processes/p/signals", requests[1].Path)
	require.JSONEq(t, `{"signal":"SIGINT"}`, string(requests[1].Body))
	require.NotEqual(t, "0", requests[2].Query.Get("timeoutMs"))
}

func TestRunCode_whenInterruptRefusedWhileQueued(t *testing.T) {
	// Given
	var submitted atomic.Bool
	routes := interpreterRunRoutes(t)
	routes["POST /processes"] = func(w http.ResponseWriter, _ *http.Request) {
		submitted.Store(true)
		writeJSON(t, w, interpreterProcessFixture("p"))
	}
	routes["POST /processes/p/signals"] = func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(t, w, map[string]any{"code": "UNSUPPORTED_OPERATION", "source": "DAYTONA_DAEMON"})
	}
	routes["GET /processes/p/wait"] = jsonHandler(t, map[string]any{"reason": "signaled", "signal": "SIGINT"})
	fake := newFakeServer(t, routes)
	sandbox := interpreterTestSandbox(t, fake)
	now := time.Now()
	sandbox.Interpreter().now = func() time.Time {
		if submitted.Load() {
			return now.Add(2 * time.Second)
		}
		return now
	}
	// When
	result, err := sandbox.RunCode(t.Context(), "loop()", &RunCodeOptions{Timeout: time.Second})
	// Then
	require.NoError(t, err)
	require.Equal(t, ProcessReasonSignaled, result.Process.Reason)
	require.Equal(t, ProcessSignalInterrupt, result.Process.Signal)
	require.Len(t, fake.requests(), 4)
}

func TestRunCode_whenIsolatedSetupSpendsBudget(t *testing.T) {
	// Given
	var created atomic.Bool
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sessions": func(w http.ResponseWriter, _ *http.Request) {
			created.Store(true)
			writeJSON(t, w, map[string]any{"id": "chosen", "kernel": "python"})
		},
		"DELETE /sessions/chosen": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
	})
	sandbox := interpreterTestSandbox(t, fake)
	now := time.Now()
	sandbox.Interpreter().now = func() time.Time {
		if created.Load() {
			return now.Add(2 * time.Second)
		}
		return now
	}
	// When
	_, err := sandbox.RunCode(t.Context(), "1", &RunCodeOptions{Isolated: true, Timeout: time.Second})
	// Then
	require.ErrorIs(t, err, ErrRequestTimeout)
	require.Len(t, fake.requests(), 2)
	require.Equal(t, "/sessions/chosen", fake.requests()[1].Path)
}

func TestRunCode_whenCallerCancelsIsolatedSetup(t *testing.T) {
	// Given
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sessions": func(_ http.ResponseWriter, r *http.Request) {
			cancel()
			<-r.Context().Done()
		},
		"DELETE /sessions/{id}": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
	})
	sandbox := interpreterTestSandbox(t, fake)
	// When
	_, err := sandbox.RunCode(ctx, "1", &RunCodeOptions{Isolated: true})
	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.Len(t, fake.requests(), 2)
	require.Equal(t, http.MethodDelete, fake.requests()[1].Method)
}
