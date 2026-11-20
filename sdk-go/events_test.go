// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"errors"
	"net"
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/require"
)

func TestEventsSourceMintsTicketAndDecodesStatesWhenWatching(t *testing.T) {
	// Given
	finished := make(chan struct{})
	subscribed := make(chan []byte, 1)
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /events/region-a/ticket": jsonHandler(t, map[string]string{"ticket": "test-ticket"}),
		"GET /events/region-a": func(w http.ResponseWriter, r *http.Request) {
			defer close(finished)
			conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{"daytona.events.v1"}})
			if err != nil {
				t.Error(err)
				return
			}
			defer closeEventsTestSocket(t, conn)
			_, payload, err := conn.Read(r.Context())
			if err != nil {
				t.Error(err)
				return
			}
			subscribed <- payload
			data := []byte(`{"type":"snapshot","id":"sbx-region-a","status":"running","version":1,"error":null}`)
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
	observations, release := client.stateSource.watch(t.Context(), "sbx-region-a", "ignored-hint")
	defer release()
	observation := eventsReceive(t, observations)

	// Then
	require.Equal(t, SandboxStateStarted, observation.State)
	require.Equal(t, "running", observation.RawState)
	require.False(t, observation.At.IsZero())
	require.JSONEq(t, `{"type":"subscribe","ids":[{"id":"sbx-region-a","known":false}]}`, string(eventsReceive(t, subscribed)))
	requests := fake.requests()
	require.Len(t, requests, 2)
	require.Equal(t, "POST", requests[0].Method)
	require.NotEmpty(t, requests[0].Header.Get("Authorization"))
	require.Empty(t, requests[0].Body)
	require.Empty(t, requests[1].Header.Get("Authorization"))
	require.Equal(t, "daytona.events.v1,daytona.ticket.test-ticket", requests[1].Header.Get("Sec-WebSocket-Protocol"))
	for _, request := range requests {
		require.Equal(t, "sdk-go", request.Header.Get("X-Daytona-Source"))
		require.NotEmpty(t, request.Header.Get("X-Daytona-SDK-Version"))
	}
}

func TestEventsDisabledReturnsNoSourceWhenConfiguredOrAmbient(t *testing.T) {
	for _, mode := range []string{"config", "0", " FaLsE ", "off"} {
		t.Run(mode, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			var opts []Option
			if mode == "config" {
				enabled := false
				opts = append(opts, WithSandboxEvents(SandboxEventsConfig{Enabled: &enabled}))
			} else {
				t.Setenv("DAYTONA_SANDBOX_EVENTS", mode)
			}
			client := newTestClient(t, fake.Server, opts...)

			// When
			ch, release := client.stateSource.watch(t.Context(), "sbx-region-a", "")
			release()

			// Then
			require.Nil(t, ch)
			require.Empty(t, fake.requests())
		})
	}
}

func TestEventsPermanentlyFallsBackWhenTicketEndpointUnavailable(t *testing.T) {
	for _, status := range []int{401, 403, 404, 501} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /events/region-a/ticket": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(status)
					writeJSON(t, w, map[string]string{"code": "WORKLOAD_PLANE_ELSEWHERE", "source": "API"})
				},
			})
			client := newTestClient(t, fake.Server)

			// When
			ch, release := client.stateSource.watch(t.Context(), "sbx-region-a", "")
			defer release()
			select {
			case _, ok := <-ch:
				require.False(t, ok)
			case <-time.After(5 * time.Second):
				t.Fatal("unavailable events did not fall back")
			}

			// Then
			next, done := client.stateSource.watch(t.Context(), "another-region-a", "")
			done()
			require.Nil(t, next)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func eventsReceive[T any](t *testing.T, ch <-chan T) T {
	t.Helper()
	select {
	case value, ok := <-ch:
		require.True(t, ok)
		return value
	case <-time.After(5 * time.Second):
		t.Fatal("events observation timed out")
		var zero T
		return zero
	}
}

func closeEventsTestSocket(t *testing.T, conn *websocket.Conn) {
	t.Helper()
	if err := conn.CloseNow(); err != nil && !errors.Is(err, net.ErrClosed) {
		t.Error(err)
	}
}

func TestEventsLearnDestinationWhenMutationHasNoResult(t *testing.T) {
	// Given
	var mu sync.Mutex
	regions := make([]string, 0, 2)
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/{id}/stop": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("X-Daytona-Cell", "shared-destination")
			writeJSON(t, w, struct{}{})
		},
		"POST /events/{region}/ticket": func(w http.ResponseWriter, r *http.Request) {
			mu.Lock()
			regions = append(regions, r.PathValue("region"))
			mu.Unlock()
			w.WriteHeader(http.StatusNotFound)
		},
	})
	client := newTestClient(t, fake.Server)
	for _, id := range []string{"sbx-region-a", "sbx-region-b"} {
		require.NoError(t, client.api().do(t.Context(), apiRequest{Method: http.MethodPost, Path: "/sandboxes/" + id + "/stop"}))
	}

	// When
	first, release := client.stateSource.watch(t.Context(), "sbx-region-a", "")
	defer release()
	for range first {
	}
	second, done := client.stateSource.watch(t.Context(), "sbx-region-b", "")
	defer done()

	// Then
	require.Nil(t, second, "both regions should use the same permanently unavailable destination")
	mu.Lock()
	defer mu.Unlock()
	require.Equal(t, []string{"region-a"}, regions)
}
