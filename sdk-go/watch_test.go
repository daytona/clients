// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

const fileEventFixture = `{"type":"future-change","name":"/a","isDir":false,"timestamp":1000}`

func fileWatchHandler(t *testing.T, frames string, hold bool) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		if _, err := io.WriteString(w, frames); err != nil {
			t.Error(err)
			return
		}
		if err := http.NewResponseController(w).Flush(); err != nil {
			t.Error(err)
			return
		}
		if hold {
			<-r.Context().Done()
		}
	}
}

func TestFileWatch_whenEarlyEventsAndOverflow_deliversInOrder(t *testing.T) {
	// Given: unknown events are skipped without parsing their data.
	frames := "event: future\ndata: not-json\n\nevent: fs\ndata: " + fileEventFixture +
		"\n\nevent: start\ndata: {}\n\nevent: overflow\ndata: {\"dropped\":true}\n\n"
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/watch": fileWatchHandler(t, frames, true)})
	fs := newFileTestService(t, fake.Server)
	var exits atomic.Int32
	watch, err := fs.Watch(t.Context(), "/", &WatchOptions{Recursive: true, OnExit: func(error) { exits.Add(1) }})
	require.NoError(t, err)
	defer watch.Stop()
	// When
	var events []FilesystemEvent
	for event, eventErr := range watch.Events(t.Context()) {
		require.NoError(t, eventErr)
		events = append(events, event)
		if len(events) == 2 {
			break
		}
	}
	require.NoError(t, watch.Wait(t.Context()))
	// Then
	require.Equal(t, "future-change", events[0].Type)
	require.Equal(t, time.Unix(1, 0).UTC(), events[0].Timestamp)
	require.True(t, events[1].Overflow)
	require.True(t, events[1].Dropped)
	require.Equal(t, int32(1), exits.Load())
	require.Equal(t, "true", fake.requests()[0].Query.Get("recursive"))
}

func TestFileWatch_whenStreamDrops_reportsExitError(t *testing.T) {
	// Given
	frames := "event: start\ndata: {}\n\nevent: fs\ndata: " + fileEventFixture + "\n\n"
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/watch": fileWatchHandler(t, frames, false)})
	fs := newFileTestService(t, fake.Server)
	watch, err := fs.Watch(t.Context(), "/", nil)
	require.NoError(t, err)
	defer watch.Stop()
	// When
	var terminal error
	var events int
	for _, eventErr := range watch.Events(t.Context()) {
		if eventErr != nil {
			terminal = eventErr
			break
		}
		events++
	}
	// Then
	require.Equal(t, 1, events)
	require.ErrorIs(t, terminal, ErrConnection)
	require.ErrorIs(t, watch.Wait(t.Context()), ErrConnection)
}

func TestFileWatch_whenStartupFails_neverCallsExit(t *testing.T) {
	cases := []struct {
		name, frames string
		limit        int64
		want         error
	}{
		{"end", "", 0, ErrConnection},
		{"malformed fs", "event: fs\ndata: {bad}\n\n", 0, ErrBadRequest},
		{"malformed overflow", "event: overflow\ndata: {}\n\n", 0, ErrBadRequest},
		{"events bound", strings.Repeat("event: overflow\ndata: {\"dropped\":true}\n\n", 4097), 0, ErrBadRequest},
		{"bytes bound", ":" + strings.Repeat("x", fileWatchStartupBytes), 0, ErrBadRequest},
		{"client bound", "event: fs\ndata: " + strings.Repeat("x", 128), 64, ErrPayloadTooLarge},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/watch": fileWatchHandler(t, tc.frames, false)})
			fs := newFileTestService(t, fake.Server, WithMaxResponseBytes(tc.limit))
			var exits atomic.Int32
			// When
			_, err := fs.Watch(t.Context(), "/", &WatchOptions{OnExit: func(error) { exits.Add(1) }})
			// Then
			require.ErrorIs(t, err, tc.want)
			require.Zero(t, exits.Load())
		})
	}
}

func TestFileWatch_whenCancelledBeforeStart_neverCallsExit(t *testing.T) {
	// Given
	opened := make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/watch": func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		if err := http.NewResponseController(w).Flush(); err != nil {
			t.Error(err)
			return
		}
		close(opened)
		<-r.Context().Done()
	}})
	fs := newFileTestService(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	var exits atomic.Int32
	done := make(chan error, 1)
	go func() {
		_, err := fs.Watch(ctx, "/", &WatchOptions{OnExit: func(error) { exits.Add(1) }})
		done <- err
	}()
	<-opened
	// When
	cancel()
	err := <-done
	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.Zero(t, exits.Load())
}

func TestFileWatch_whenExitCallbackCallsStop_noDeadlock(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/watch": fileWatchHandler(t, "event: start\ndata: {}\n\n", true)})
	fs := newFileTestService(t, fake.Server)
	var watch *WatchHandle
	watch, err := fs.Watch(t.Context(), "/", &WatchOptions{OnExit: func(error) { watch.Stop(); panic("contained") }})
	require.NoError(t, err)
	// When
	watch.Stop()
	// Then
	require.NoError(t, watch.Wait(t.Context()))
}
