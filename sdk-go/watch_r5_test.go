// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFileWatch_whenOverflowPayloadMalformed_namesOverflow(t *testing.T) {
	for _, payload := range []string{"{bad}", "[]", "null", "{}", `{"dropped":null}`, `{"dropped":"yes"}`} {
		t.Run(payload, func(t *testing.T) {
			// Given an overflow event before the watch starts.
			frames := "event: overflow\ndata: " + payload + "\n\n"
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /files/watch": fileWatchHandler(t, frames, false),
			})
			fs := newFileTestService(t, fake.Server)
			// When the malformed event is received.
			watch, err := fs.Watch(t.Context(), "/", nil)
			// Then the error identifies the overflow payload.
			require.Nil(t, watch)
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, "filesystem watch overflow event")
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, "BAD_REQUEST", sdkErr.Code)
			require.Equal(t, http.StatusBadRequest, sdkErr.StatusCode)
			require.Equal(t, "SDK", sdkErr.Source)
		})
	}
}
