// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"io"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReadMany_whenFramingMalformed_reportsMultipartError(t *testing.T) {
	for _, tc := range []struct {
		name string
		body string
	}{
		{"headers", "--boundary\r\ninvalid-header\r\n\r\nbody\r\n--boundary--\r\n"},
		{"truncated body", "--boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"/a\"\r\n\r\nbody"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given complete HTTP transport framing but malformed multipart framing.
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /files/bulk-download": func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "multipart/form-data; boundary=boundary")
				if _, err := io.WriteString(w, tc.body); err != nil {
					t.Error(err)
				}
			}})
			fs := newFileTestService(t, fake.Server)

			// When parsing the bulk response.
			result, err := fs.ReadMany(t.Context(), []string{"/a"}, nil)

			// Then protocol diagnostics identify multipart, not JSON.
			require.Nil(t, result)
			require.ErrorIs(t, err, ErrBadRequest)
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, "SDK", sdkErr.Source)
			require.Contains(t, sdkErr.Message, "multipart")
			require.NotContains(t, sdkErr.Message, "JSON")
			if tc.name == "truncated body" {
				require.ErrorIs(t, err, io.ErrUnexpectedEOF)
			}
		})
	}
}
