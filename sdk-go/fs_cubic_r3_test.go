// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"io"
	"math"
	"net/http"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFileGrep_whenLineExceedsInt32_rejectsResponse(t *testing.T) {
	for _, line := range []int64{math.MaxInt32, math.MaxInt32 + 1} {
		t.Run(strconv.FormatInt(line, 10), func(t *testing.T) {
			// Given a match at or beyond the contract's int32 limit.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /files/find": jsonHandler(t, []map[string]any{{"file": "/a", "line": line, "content": "match"}}),
			})
			fs := newFileTestService(t, fake.Server)
			// When reading the match.
			result, err := fs.Grep(t.Context(), "/", "match")
			// Then only a line within the wire range is accepted.
			if line > math.MaxInt32 {
				require.ErrorIs(t, err, ErrBadRequest)
				require.ErrorContains(t, err, "line")
				return
			}
			require.NoError(t, err)
			require.Equal(t, line, result.Items[0].Line)
		})
	}
}

func TestReadMany_whenPartHasNoDisposition_skipsExtension(t *testing.T) {
	// Given an extension part precedes a file and a per-file error.
	body := "--boundary\r\nContent-Type: application/octet-stream\r\n\r\nextension\r\n" +
		"--boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"/a\"\r\n\r\ncontent\r\n" +
		"--boundary\r\nContent-Disposition: form-data; name=\"error\"\r\n\r\n" +
		"{\"path\":\"/missing\",\"message\":\"not found\"}\r\n--boundary--\r\n"
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /files/bulk-download": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "multipart/form-data; boundary=boundary")
			if _, err := io.WriteString(w, body); err != nil {
				t.Error(err)
			}
		},
	})
	fs := newFileTestService(t, fake.Server)
	// When parsing the bulk read.
	result, err := fs.ReadMany(t.Context(), []string{"/a", "/missing"}, nil)
	// Then the extension does not discard either useful part.
	require.NoError(t, err)
	require.Equal(t, map[string][]byte{"/a": []byte("content")}, result.Files)
	require.Equal(t, []BulkReadError{{Path: "/missing", Message: "not found"}}, result.Errors)
}
