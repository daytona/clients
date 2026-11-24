// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"io"
	"net/http"
	"syscall"
	"testing"

	"github.com/stretchr/testify/require"
)

type downloadFailingWriter struct{ failure error }

func (w downloadFailingWriter) Write([]byte) (int, error) { return 0, w.failure }

func TestDownload_whenDestinationFails_preservesLocalCause(t *testing.T) {
	for _, test := range []struct {
		name    string
		failure error
		cause   error
	}{
		{"full disk", syscall.ENOSPC, syscall.ENOSPC},
		{"short write", nil, io.ErrShortWrite},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given a healthy response and a destination that cannot accept it.
			sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
				"GET /files/download": func(w http.ResponseWriter, _ *http.Request) {
					if _, err := io.WriteString(w, "payload"); err != nil {
						t.Error(err)
					}
				},
			})
			// When downloading to the failing caller-owned writer.
			err := sandbox.FS().Download(t.Context(), "/file", downloadFailingWriter{test.failure}, nil)
			// Then a retry policy sees local I/O, not a connection failure.
			require.ErrorIs(t, err, test.cause)
			require.NotErrorIs(t, err, ErrConnection)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Same(t, sdkError, err)
			require.Equal(t, "write download destination: "+test.cause.Error(), sdkError.Message)
		})
	}
}

func TestDownload_whenResponseIsTruncated_preservesConnectionError(t *testing.T) {
	// Given the peer promises more bytes than it sends.
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /files/download": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Length", "100")
			if _, err := io.WriteString(w, "partial"); err != nil {
				t.Error(err)
			}
		},
	})
	// When the destination itself is healthy.
	err := sandbox.FS().Download(t.Context(), "/file", io.Discard, nil)
	// Then source failures still carry the retryable connection category.
	require.ErrorIs(t, err, ErrConnection)
	require.ErrorIs(t, err, io.ErrUnexpectedEOF)
}
