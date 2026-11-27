// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

// MoveOptions.Timeout replaces the client's request timeout for one move,
// against a delayed response; a move is never retried. The Python and
// TypeScript suites assert the same behaviour.

func moveTestSandbox(t *testing.T, delay, requestTimeout time.Duration) (*Sandbox, *atomic.Int32) {
	t.Helper()
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/files/move" {
			http.NotFound(w, r)
			return
		}
		requests.Add(1)
		select {
		case <-time.After(delay):
			w.WriteHeader(http.StatusOK)
		case <-r.Context().Done():
		}
	}))
	t.Cleanup(server.Close)
	client := newTestClient(t, server, WithRequestTimeout(requestTimeout))
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetSandboxOptions{ToolboxURL: server.URL, AccessToken: strings.Join([]string{"test", "sandbox"}, "-")},
	})
	require.NoError(t, err)
	return sandbox, &requests
}

func TestMoveKeepsTheRequestTimeoutAndIsNotRetried(t *testing.T) {
	sandbox, requests := moveTestSandbox(t, 400*time.Millisecond, 100*time.Millisecond)
	for _, opts := range [][]*MoveOptions{nil, {nil}, {{}}} {
		err := sandbox.FS().Move(t.Context(), "/big", "/mnt/big", opts...)
		require.ErrorIs(t, err, ErrClientTimeout)
	}
	require.Equal(t, int32(3), requests.Load())
}

func TestMoveTimeoutOutlastsAShorterRequestTimeout(t *testing.T) {
	// The move answers after 0.4 s, past the 0.1 s request timeout: a longer
	// per-call timeout, NoTimeout and one above the timer maximum let it finish.
	for _, timeout := range []time.Duration{2 * time.Second, NoTimeout, maxTimerDuration + time.Second} {
		sandbox, requests := moveTestSandbox(t, 400*time.Millisecond, 100*time.Millisecond)
		require.NoError(t, sandbox.FS().Move(t.Context(), "/big", "/mnt/big", &MoveOptions{Timeout: timeout}))
		require.Equal(t, int32(1), requests.Load())
	}
}

func TestMoveShorterTimeoutEndsTheCallOnce(t *testing.T) {
	sandbox, requests := moveTestSandbox(t, 400*time.Millisecond, 30*time.Second)
	err := sandbox.FS().Move(t.Context(), "/big", "/mnt/big", &MoveOptions{Timeout: 100 * time.Millisecond})
	require.ErrorIs(t, err, ErrClientTimeout)
	require.Equal(t, int32(1), requests.Load())
}

func TestMoveRefusesInvalidOptionsBeforeAnyRequest(t *testing.T) {
	sandbox, requests := moveTestSandbox(t, 0, 30*time.Second)
	for _, opts := range [][]*MoveOptions{{{Timeout: -time.Second}}, {{Timeout: WaitForever}}, {{}, {}}} {
		err := sandbox.FS().Move(t.Context(), "/big", "/mnt/big", opts...)
		require.ErrorIs(t, err, ErrBadRequest)
	}
	require.Equal(t, int32(0), requests.Load())
}
