// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"strings"
	"testing"
	"testing/iotest"

	"github.com/stretchr/testify/require"
)

func TestProcessSSECountsCRLFWhenSplitAcrossReads(t *testing.T) {
	for _, terminator := range []string{"\n", "\r\n", "\r"} {
		t.Run(strings.ReplaceAll(terminator, "\n", "LF"), func(t *testing.T) {
			// Given an event received one byte at a time.
			body := "event: eof" + terminator + `data: {"cursor":"1"}` + terminator + terminator
			for _, allowance := range []int{len(body), len(body) - 1} {
				reader := newProcessSSEReader(iotest.OneByteReader(strings.NewReader(body)), int64(allowance))
				// When reading under an exact or one-byte-too-small cap.
				message, err := reader.next()
				// Then framing bytes count, including split CRLF and the final blank line.
				if allowance == len(body) {
					require.NoError(t, err)
					require.Equal(t, "eof", message.event)
				} else {
					require.ErrorIs(t, err, ErrPayloadTooLarge)
				}
			}
		})
	}
}
