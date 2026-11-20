// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bufio"
	"io"
	"strings"
	"testing"
	"testing/iotest"

	"github.com/stretchr/testify/require"
)

func TestFileSSE_whenDeferredLFEndsStart_preservesStartupLimit(t *testing.T) {
	for _, test := range []struct {
		name  string
		extra int
	}{{"exact", 0}, {"overflow", 1}} {
		t.Run(test.name, func(t *testing.T) {
			// Given: the start frame's final optional LF is delayed.
			start := "event: start\rdata: {}\r\r\n"
			prefix := ":" + strings.Repeat("x", fileWatchStartupBytes+test.extra-len(start)-3) + "\n\n"
			body := prefix + start
			parser := &fileSSE{reader: bufio.NewReader(iotest.OneByteReader(strings.NewReader(body)))}

			// When: the handshake is accepted before its optional LF arrives.
			message, err := parser.next()
			require.NoError(t, err)
			require.Equal(t, "start", message.event)
			parser.started = true
			_, err = parser.next()

			// Then: changing startup state cannot exempt the start frame's LF.
			if test.extra == 0 {
				require.ErrorIs(t, err, io.EOF)
				require.Equal(t, int64(len(body)), parser.completed)
			} else {
				require.ErrorIs(t, err, ErrBadRequest)
			}
		})
	}
}

func TestFileSSE_whenDeferredLFEndsFrame_preservesPerFrameLimit(t *testing.T) {
	for _, ending := range []string{"\r\n", "\r", "\n"} {
		t.Run(ending, func(t *testing.T) {
			// Given: two exactly capped frames, received one byte at a time.
			frame := "data: x" + ending + ending
			parser := &fileSSE{
				reader: bufio.NewReader(iotest.OneByteReader(strings.NewReader(frame + frame))),
				limit:  int64(len(frame)), started: true,
			}

			// When: draining both frames and the final optional LF.
			var messages []string
			var terminal error
			for {
				message, err := parser.next()
				if err != nil {
					terminal = err
					break
				}
				messages = append(messages, message.data)
			}

			// Then: frame resets preserve both exact bounds and total counts.
			require.ErrorIs(t, terminal, io.EOF)
			require.Equal(t, []string{"x", "x"}, messages)
			require.Equal(t, int64(2*len(frame)), parser.completed)
		})
	}
}

func TestFileSSE_whenDeferredLFExceedsFrameLimit_rejectsBeforeNextFrame(t *testing.T) {
	for _, frame := range []string{"data: x\r\n\r\n", ": heartbeat\r\n\r\n"} {
		t.Run(frame, func(t *testing.T) {
			// Given: only the final LF exceeds the previous frame's cap.
			parser := &fileSSE{
				reader: bufio.NewReader(iotest.OneByteReader(strings.NewReader(frame + "data: y\n\n"))),
				limit:  int64(len(frame) - 1), started: true,
			}

			// When: reading until the deferred byte is inspected.
			_, err := parser.next()
			if err == nil {
				_, err = parser.next()
			}

			// Then: the LF is not charged to the smaller following frame.
			require.ErrorIs(t, err, ErrPayloadTooLarge)
		})
	}
}
