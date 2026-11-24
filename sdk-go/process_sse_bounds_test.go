// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"io"
	"strings"
	"testing"
	"testing/iotest"

	"github.com/stretchr/testify/require"
)

func TestProcessSSE_whenDeferredLFEndsFrame_preservesPerFrameLimit(t *testing.T) {
	for _, ending := range []string{"\r\n", "\r", "\n"} {
		t.Run(ending, func(t *testing.T) {
			// Given: two exactly capped frames, received one byte at a time.
			frame := "data: x" + ending + ending
			parser := newProcessSSEReader(iotest.OneByteReader(strings.NewReader(frame+frame)), int64(len(frame)))

			// When: draining the complete stream, including any deferred LF.
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

			// Then: neither frame inherits the other's framing bytes.
			require.ErrorIs(t, terminal, io.EOF)
			require.Equal(t, []string{"x", "x"}, messages)
		})
	}
}

func TestProcessSSE_whenDeferredLFEndsCommentFrame_checksPreviousLimit(t *testing.T) {
	// Given: a comment-only frame whose final optional LF exceeds its cap.
	frame := ": heartbeat\r\n\r\n"
	parser := newProcessSSEReader(iotest.OneByteReader(strings.NewReader(frame+"data: x\n\n")), int64(len(frame)-1))

	// When
	_, err := parser.next()

	// Then: ignoring a comment frame never drops its deferred byte bound.
	require.ErrorIs(t, err, ErrPayloadTooLarge)
}
