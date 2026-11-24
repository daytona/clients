// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFileWatch_whenSSELineEndingsAndIncompleteBlocks(t *testing.T) {
	for _, ending := range []string{"\n", "\r\n", "\r"} {
		t.Run(ending, func(t *testing.T) {
			// Given: a data-less frame and an unterminated frame must never dispatch.
			frames := "\ufeffevent: start\ndata: {}\n\nevent: fs\n\nevent: fs\ndata: " +
				fileEventFixture + "\n\nevent: fs\ndata: " + fileEventFixture
			frames = strings.ReplaceAll(frames, "\n", ending)
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/watch": fileWatchHandler(t, frames, false)})
			fs := newFileTestService(t, fake.Server)
			watch, err := fs.Watch(t.Context(), "/", nil)
			require.NoError(t, err)
			defer watch.Stop()
			// When
			var count int
			var terminal error
			for _, eventErr := range watch.Events(t.Context()) {
				if eventErr != nil {
					terminal = eventErr
					break
				}
				count++
			}
			// Then
			require.Equal(t, 1, count)
			require.ErrorIs(t, terminal, ErrConnection)
			require.ErrorIs(t, watch.Wait(t.Context()), ErrConnection)
		})
	}
}

func TestFileWatch_whenPostStartFrameTooLarge(t *testing.T) {
	// Given
	frames := "event: start\ndata: {}\n\n" + "event: future\ndata: " + strings.Repeat("x", 100)
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/watch": fileWatchHandler(t, frames, false)})
	fs := newFileTestService(t, fake.Server, WithMaxResponseBytes(64))
	watch, err := fs.Watch(t.Context(), "/", nil)
	require.NoError(t, err)
	defer watch.Stop()
	// When
	err = watch.Wait(t.Context())
	// Then
	require.ErrorIs(t, err, ErrPayloadTooLarge)
}

func TestFileWatch_whenLimitsExactlyReached_acceptsStart(t *testing.T) {
	for _, frames := range []string{
		strings.Repeat("event: overflow\ndata: {\"dropped\":true}\n\n", fileWatchStartupEvents) + "event: start\ndata: {}\n\n",
		":" + strings.Repeat("x", fileWatchStartupBytes-len("event: start\ndata: {}\n\n")-3) + "\n\nevent: start\ndata: {}\n\n",
	} {
		// Given
		fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/watch": fileWatchHandler(t, frames, true)})
		fs := newFileTestService(t, fake.Server)
		// When
		watch, err := fs.Watch(t.Context(), "/", nil)
		// Then
		require.NoError(t, err)
		watch.Stop()
		require.NoError(t, watch.Wait(t.Context()))
	}
}

func TestFileWatch_whenStopped_keepsPeerAndClientUsable(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /files/watch": fileWatchHandler(t, "event: start\ndata: {}\n\n", true),
		"GET /files/info":  jsonHandler(t, fileInfoFixture()),
	})
	fs := newFileTestService(t, fake.Server)
	first, err := fs.Watch(t.Context(), "/", nil)
	require.NoError(t, err)
	defer first.Stop()
	second, err := fs.Watch(t.Context(), "/", nil)
	require.NoError(t, err)
	defer second.Stop()
	// When
	first.Stop()
	require.NoError(t, first.Wait(t.Context()))
	// Then
	require.NoError(t, second.Err())
	_, err = fs.Stat(t.Context(), "/a")
	require.NoError(t, err)
	second.Stop()
	require.NoError(t, second.Wait(t.Context()))
}

func TestFileWatch_whenCancelledBeforeCall_neverCallsExit(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{})
	fs := newFileTestService(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	var exits atomic.Int32
	// When
	_, err := fs.Watch(ctx, "/", &WatchOptions{OnExit: func(error) { exits.Add(1) }})
	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.Zero(t, exits.Load())
	require.Empty(t, fake.requests())
}
