// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"io"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReadMany_whenQuotedFilenameContainsParameterText_preservesPath(t *testing.T) {
	for _, test := range []struct {
		name, filename, disposition string
	}{
		{"semicolon", "/a; filename*=utf-8''wrong", `filename="/a; filename*=utf-8''wrong"`},
		{"escaped quote", `/a"; filename*=utf-8''wrong`, `filename="/a\"; filename*=utf-8''wrong"`},
		{"extended after quoted value", "/real", `filename="/a; filename*=utf-8''wrong"; filename*=utf-8''%2Freal`},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given a quoted filename with text that is not a header parameter.
			body := "--DAYTONA-FILE-BOUNDARY\r\nContent-Disposition: form-data; name=\"file\"; " +
				test.disposition + "\r\n\r\npayload\r\n--DAYTONA-FILE-BOUNDARY--\r\n"
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /files/bulk-download": func(w http.ResponseWriter, _ *http.Request) {
				if _, err := io.WriteString(w, body); err != nil {
					t.Error(err)
				}
			}})
			fs := newFileTestService(t, fake.Server)

			// When decoding the bulk response.
			result, err := fs.ReadMany(t.Context(), []string{test.filename}, nil)

			// Then only real filename* parameters override the quoted filename.
			require.NoError(t, err)
			require.Equal(t, map[string][]byte{test.filename: []byte("payload")}, result.Files)
		})
	}
}
