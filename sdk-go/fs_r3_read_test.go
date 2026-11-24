// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"io"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReadFile_whenJSONLabelled_preservesExactBytes(t *testing.T) {
	for _, body := range []string{`{"unfinished":`, "\x00\xff", "", `{"valid":true}`} {
		t.Run(body, func(t *testing.T) {
			// Given arbitrary file bytes labelled as JSON.
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/download": func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json; charset=utf-8")
				if _, err := io.WriteString(w, body); err != nil {
					t.Error(err)
				}
			}})
			fs := newFileTestService(t, fake.Server)

			// When reading bytes rather than decoding a JSON response.
			data, err := fs.ReadFile(t.Context(), "/a", nil)

			// Then the content type cannot reject or transform the file.
			require.NoError(t, err)
			require.Equal(t, body, string(data))
		})
	}
}
