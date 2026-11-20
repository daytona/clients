// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bufio"
	"io"
	"net/http"
	"strings"
	"testing"
	"testing/iotest"

	"github.com/stretchr/testify/require"
)

func TestFileWatch_whenCRLFTerminatorExceedsFrameLimit(t *testing.T) {
	for _, frame := range []string{
		"event: future\r\ndata: " + strings.Repeat("x", 32) + "\r\n\r\n",
		": " + strings.Repeat("x", 32) + "\r\n\r\n",
	} {
		t.Run(frame, func(t *testing.T) {
			// Given: the final LF is the only byte over the limit.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /files/watch": fileWatchHandler(t, "event: start\ndata: {}\n\n"+frame, false),
			})
			fs := newFileTestService(t, fake.Server, WithMaxResponseBytes(int64(len(frame)-1)))
			watch, err := fs.Watch(t.Context(), "/", nil)
			require.NoError(t, err)
			defer watch.Stop()
			// When
			err = watch.Wait(t.Context())
			// Then
			require.ErrorIs(t, err, ErrPayloadTooLarge)
		})
	}
}

func TestFileSSE_whenLineTerminatorEndsAtReadBoundary(t *testing.T) {
	for _, ending := range []string{"\r\n", "\r", "\n"} {
		t.Run(ending, func(t *testing.T) {
			// Given: every byte arrives separately, and EOF follows the terminator.
			frame := "data: first" + ending + ending
			parser := &fileSSE{
				reader: bufio.NewReader(iotest.OneByteReader(strings.NewReader(frame))),
				limit:  int64(len(frame)), started: true,
			}
			// When
			message, err := parser.next()
			// Then
			require.NoError(t, err)
			require.Equal(t, "first", message.data)
			// A split optional LF is accounted on the next read, not by
			// blocking delivery of the CR-terminated event.
			_, err = parser.next()
			require.ErrorIs(t, err, io.EOF)
			require.Equal(t, int64(len(frame)), parser.completed)
		})
	}
}
