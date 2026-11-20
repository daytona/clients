// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/require"
)

func TestEventsObservationsNormalizeWireStatesWhenWatching(t *testing.T) {
	for raw, want := range map[string]SandboxState{
		"queued": SandboxStateQueued, "pending": SandboxStateCreating, "assigned": SandboxStateCreating,
		"booting": SandboxStateStarting, "running": SandboxStateStarted, "pausing": SandboxStatePausing,
		"paused": SandboxStatePaused, "stopping": SandboxStateStopping, "stopped": SandboxStateStopped,
		"deleting": SandboxStateDestroying, "deleted": SandboxStateDestroyed, "failed": SandboxStateError,
		"lost": SandboxStateError, "future-state": SandboxStateUnknown,
	} {
		t.Run(raw, func(t *testing.T) {
			// Given
			finished := make(chan struct{})
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /events/_/ticket": jsonHandler(t, map[string]string{"ticket": "test-ticket"}),
				"GET /events/_": func(w http.ResponseWriter, r *http.Request) {
					defer close(finished)
					conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{"daytona.events.v1"}})
					if err != nil {
						t.Error(err)
						return
					}
					defer closeEventsTestSocket(t, conn)
					if _, _, err = conn.Read(r.Context()); err != nil {
						t.Error(err)
						return
					}
					data, err := json.Marshal(map[string]any{
						"type": "snapshot", "id": "bare", "status": raw, "version": 1, "error": "reported reason",
					})
					if err != nil {
						t.Error(err)
						return
					}
					if err = conn.Write(r.Context(), websocket.MessageText, data); err != nil {
						t.Error(err)
						return
					}
					for {
						if _, _, readErr := conn.Read(r.Context()); readErr != nil {
							return
						}
					}
				},
			})
			client := newTestClient(t, fake.Server)
			t.Cleanup(func() {
				require.NoError(t, client.Close())
				<-finished
			})

			// When
			ch, release := client.stateSource.watch(t.Context(), "bare", "local")
			defer release()
			observation := eventsReceive(t, ch)

			// Then
			require.Equal(t, want, observation.State)
			require.Equal(t, raw, observation.RawState)
			require.Equal(t, "reported reason", observation.Reason)
		})
	}
}

func TestEventsConnectionKeepsBasePathWhenAPIHasPrefix(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /prefix/events/a/ticket": jsonHandler(t, map[string]string{"ticket": "test-ticket"}),
		"GET /prefix/events/a": func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusServiceUnavailable)
		},
	})
	newTestClient(t, fake.Server) // Install isolated credential environment.
	t.Setenv("DAYTONA_V2_API_URL", fake.Server.URL+"/prefix")
	client, err := New()
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, client.Close()) })

	// When
	_, err = client.connectSandboxEvents(t.Context(), "a")

	// Then
	require.ErrorIs(t, err, ErrServiceUnavailable)
	requests := fake.requests()
	require.Len(t, requests, 2)
	require.Equal(t, "/prefix/events/a/ticket", requests[0].Path)
	require.Equal(t, "/prefix/events/a", requests[1].Path)
}
