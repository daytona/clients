// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestContextUploadRejectsOutOfRangeStoragePort(t *testing.T) {
	for _, port := range []string{"65536", "99999", "18446744073709551616"} {
		t.Run(port, func(t *testing.T) {
			// Given a presigned URL with a numeric port outside the TCP range.
			endpoint := "http://127.0.0.1:" + port + "/part?signature=redacted"
			api := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /build-contexts": jsonHandler(t, contextTestDeclaration([]map[string]any{
					contextTestPart(1, 3, endpoint),
				})),
			})
			client := newTestClient(t, api.Server)
			uploader := contextTestUploader(t, client, []byte("abc"))
			var pauses int
			uploader.pause = func(context.Context, time.Duration) error {
				pauses++
				return nil
			}

			// When uploading the declared context.
			_, err := uploader.upload(t.Context(), "")

			// Then the malformed response fails before storage retries or completion.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Zero(t, pauses)
			require.NoError(t, errors.Unwrap(err))
			require.NotContains(t, err.Error(), endpoint)
			require.Len(t, api.requests(), 1)
		})
	}
}

func TestContextStorageURLAcceptsPortBoundaries(t *testing.T) {
	for _, endpoint := range []string{
		"https://storage.invalid/part",
		"https://storage.invalid:0/part",
		"https://storage.invalid:65535/part",
		"https://storage.invalid:00065535/part",
		"http://[::1]:65535/part",
	} {
		t.Run(endpoint, func(t *testing.T) {
			// Given a storage URL with an absent or valid numeric port.
			// When validating the response URL.
			err := contextStorageURL(endpoint)
			// Then the port is accepted without accessing storage.
			require.NoError(t, err)
		})
	}
}
