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

func TestWait_whenTargetsAreValid_mapsWireStates(t *testing.T) {
	for _, status := range []string{"queued", "booting", "running", "pausing", "paused", "stopping", "stopped", "deleting", "deleted"} {
		t.Run(status, func(t *testing.T) {
			// Given
			detail := sandboxFixture("sbx")
			detail["status"] = status
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": jsonHandler(t, detail)})
			sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
			require.NoError(t, err)
			// When
			err = sandbox.Wait(t.Context(), &WaitOptions{Until: []SandboxState{sandboxState(status)}})
			// Then
			require.NoError(t, err)
			query := "wait"
			if status == "deleted" {
				query = "wait_exact"
			}
			require.Equal(t, status, fake.requests()[0].Query.Get(query))
		})
	}
}

func TestWait_whenInvalid_doesNotRead(t *testing.T) {
	for _, states := range [][]SandboxState{
		{}, {SandboxStateCreating}, {SandboxStateError}, {SandboxStateUnknown}, {"running"},
	} {
		// Given
		fake := newFakeServer(t, nil)
		sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
		require.NoError(t, err)
		// When
		err = sandbox.Wait(t.Context(), &WaitOptions{Until: states})
		// Then
		require.ErrorIs(t, err, ErrBadRequest)
		require.Empty(t, fake.requests())
	}
}

func TestWait_whenSeveralTargets_usesPlainRead(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": jsonHandler(t, sandboxFixture("sbx"))})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	// When
	err = sandbox.Wait(t.Context(), &WaitOptions{Until: []SandboxState{SandboxStateStarted, SandboxStateStopped}})
	// Then
	require.NoError(t, err)
	require.Empty(t, fake.requests()[0].Query)
}

func TestWait_whenUnknownState_keepsPolling(t *testing.T) {
	// Given
	var calls atomic.Int32
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": func(w http.ResponseWriter, _ *http.Request) {
		detail := sandboxFixture("sbx")
		if calls.Add(1) == 1 {
			detail["status"] = "future-status"
		}
		writeJSON(t, w, detail)
	}})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	// When
	err = sandbox.Wait(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxStateStarted, sandbox.State())
	require.EqualValues(t, 2, calls.Load())
}

func TestWait_whenCancelled_releasesRead(t *testing.T) {
	// Given
	entered := make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": func(_ http.ResponseWriter, r *http.Request) {
		close(entered)
		<-r.Context().Done()
	}})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- sandbox.Wait(ctx, &WaitOptions{Timeout: WaitForever})
	}()
	<-entered
	// When
	cancel()
	// Then
	require.ErrorIs(t, <-done, context.Canceled)
}

func TestWait_whenServerTimeout_preservesServerError(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusRequestTimeout)
		writeJSON(t, w, map[string]string{"code": CodeRequestTimeout, "source": "DAYTONA_API", "message": "server timeout"})
	}})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	// When
	err = sandbox.Wait(t.Context(), &WaitOptions{Timeout: time.Second})
	// Then
	var sdkError *Error
	require.ErrorAs(t, err, &sdkError)
	require.Equal(t, "DAYTONA_API", sdkError.Source)
	require.NotErrorIs(t, err, ErrClientTimeout)
}
