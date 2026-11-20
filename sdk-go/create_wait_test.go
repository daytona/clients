// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strconv"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCreateWaitReturnsLastHandleAfterReadFailure(t *testing.T) {
	for _, route := range createTestCases() {
		for _, status := range []int{403, 404, 408, 503} {
			t.Run(route.name+"/"+http.StatusText(status), func(t *testing.T) {
				// Given
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST " + route.path: jsonHandler(t, map[string]any{"id": "sbx", "status": "pending"}),
					"GET /sandboxes/sbx": func(w http.ResponseWriter, _ *http.Request) {
						w.Header().Set("Content-Type", "application/json")
						w.WriteHeader(status)
						writeJSON(t, w, map[string]string{"code": "REQUEST_TIMEOUT", "source": "DAYTONA_API"})
					},
				})
				client := newTestClient(t, fake.Server)
				// When
				sandbox, err := route.call(t.Context(), client, &CreateFromSnapshotOptions{Wait: time.Minute})
				// Then
				require.NoError(t, err)
				require.Equal(t, SandboxStateCreating, sandbox.State())
				require.Len(t, fake.requests(), 2)
				require.Equal(t, "running", fake.requests()[1].Query.Get("wait"))
			})
		}
	}
}

func TestCreateWaitReturnsTerminalOrReadyObservation(t *testing.T) {
	for _, state := range []string{"running", "failed"} {
		t.Run(state, func(t *testing.T) {
			// Given
			detail := sandboxFixture("sbx")
			detail["status"], detail["error"] = state, "server detail"
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes":    jsonHandler(t, map[string]any{"id": "sbx", "status": "pending"}),
				"GET /sandboxes/sbx": jsonHandler(t, detail),
			})
			client := newTestClient(t, fake.Server)
			// When
			sandbox, err := client.Create(t.Context(), "", nil)
			// Then
			require.NoError(t, err)
			require.Equal(t, sandboxState(state), sandbox.State())
			require.Equal(t, "server detail", sandbox.Info().Error)
			require.Len(t, fake.requests(), 2)
		})
	}
}

func TestCreateWaitHonorsServerHoldCap(t *testing.T) {
	for _, tc := range []struct {
		name  string
		cap   any
		reads int
	}{
		{"full hold", 60, 0},
		{"large hold", 1e100, 0},
		{"partial hold", 59.75, 1},
		{"negative", -1, 1},
		{"mistyped", "60", 1},
		{"absent", nil, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes":    jsonHandler(t, map[string]any{"id": "sbx", "status": "pending", "wait_cap_seconds": tc.cap}),
				"GET /sandboxes/sbx": jsonHandler(t, sandboxFixture("sbx")),
			})
			client := newTestClient(t, fake.Server)
			// When
			sandbox, err := client.Create(t.Context(), "", nil)
			// Then
			require.NoError(t, err)
			require.Len(t, fake.requests(), tc.reads+1)
			if tc.reads == 0 {
				require.Equal(t, SandboxStateCreating, sandbox.State())
			} else {
				require.Equal(t, SandboxStateStarted, sandbox.State())
			}
			if tc.name == "partial hold" {
				millis, parseErr := strconv.Atoi(fake.requests()[1].Query.Get("timeout_ms"))
				require.NoError(t, parseErr)
				require.Positive(t, millis)
				require.LessOrEqual(t, millis, 250)
			}
		})
	}
}

func TestCreateWaitReturnsHandleWhenBudgetExpires(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes":    jsonHandler(t, map[string]any{"id": "sbx", "status": "pending", "wait_cap_seconds": 0.975}),
		"GET /sandboxes/sbx": func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() },
	})
	client := newTestClient(t, fake.Server)
	// When
	sandbox, err := client.Create(t.Context(), "", &CreateOptions{Wait: time.Second})
	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxStateCreating, sandbox.State())
	require.Len(t, fake.requests(), 2)
}

func TestCreateWaitRejectsMalformedReadWithoutLosingAcceptedHandle(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes":    jsonHandler(t, map[string]any{"id": "sbx", "status": "pending"}),
		"GET /sandboxes/sbx": jsonHandler(t, map[string]any{"id": "sbx"}),
	})
	client := newTestClient(t, fake.Server)
	// When
	sandbox, err := client.Create(t.Context(), "", nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxStateCreating, sandbox.State())
	require.Len(t, fake.requests(), 2)
}
