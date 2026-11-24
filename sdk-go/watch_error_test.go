// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strconv"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

const watchErrorFrame = "event: error\ndata: " +
	`{"code":"FILE_ACCESS_DENIED","message":"access denied","source":"DAYTONA_DAEMON"}` + "\n\n"

func TestFileWatch_whenServerErrorAfterStart_reportsTypedExitError(t *testing.T) {
	// Given
	frames := "event: start\ndata: {}\n\n" + watchErrorFrame +
		"event: fs\ndata: " + fileEventFixture + "\n\n"
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/watch": fileWatchHandler(t, frames, false)})
	fs := newFileTestService(t, fake.Server)
	exits := make(chan error, 1)
	watch, err := fs.Watch(t.Context(), "/", &WatchOptions{OnExit: func(err error) { exits <- err }})
	require.NoError(t, err)
	defer watch.Stop()
	// When
	var terminal error
	var events []FilesystemEvent
	for event, eventErr := range watch.Events(t.Context()) {
		if eventErr != nil {
			terminal = eventErr
			break
		}
		events = append(events, event)
	}
	require.ErrorIs(t, watch.Wait(t.Context()), ErrFileAccessDenied)
	// Then
	require.ErrorIs(t, terminal, ErrFileAccessDenied)
	var sdkError *Error
	require.ErrorAs(t, terminal, &sdkError)
	require.Equal(t, "access denied", sdkError.Message)
	require.Same(t, terminal, watch.Err())
	require.Same(t, terminal, <-exits)
	require.Empty(t, events)
}

func TestFileWatch_whenServerErrorBeforeStart_failsWithTypedError(t *testing.T) {
	for _, count := range []int{0, fileWatchStartupEvents} {
		t.Run(strconv.Itoa(count)+" early events", func(t *testing.T) {
			// Given: even at the held-event cap, error is terminal, not another held event.
			frames := strings.Repeat("event: overflow\ndata: {\"dropped\":true}\n\n", count) + watchErrorFrame
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/watch": fileWatchHandler(t, frames, false)})
			fs := newFileTestService(t, fake.Server)
			exits := 0
			// When
			_, err := fs.Watch(t.Context(), "/", &WatchOptions{OnExit: func(error) { exits++ }})
			// Then
			require.ErrorIs(t, err, ErrFileAccessDenied)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Equal(t, "access denied", sdkError.Message)
			require.Zero(t, exits)
		})
	}
}

func TestFileWatch_whenErrorMessageIsNotString_reportsMalformedResponse(t *testing.T) {
	for _, start := range []string{"", "event: start\ndata: {}\n\n"} {
		t.Run(start, func(t *testing.T) {
			// Given
			frames := start + "event: error\ndata: {\"code\":\"FILE_ACCESS_DENIED\",\"message\":123}\n\n"
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/watch": fileWatchHandler(t, frames, false)})
			fs := newFileTestService(t, fake.Server)
			// When
			watch, err := fs.Watch(t.Context(), "/", nil)
			if watch != nil {
				defer watch.Stop()
				err = watch.Wait(t.Context())
			}
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
