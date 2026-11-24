// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestWriteMany_whenAtomicUnsupported_keepsPartialSuccess(t *testing.T) {
	// Given one successful file and one human-readable atomic replacement failure.
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /files/bulk-upload": jsonHandler(t,
		map[string]any{
			"files":  []any{map[string]any{"path": "/a", "bytes": 1}},
			"errors": []string{"/b: atomic replacement unsupported"},
		})})
	fs := newFileTestService(t, fake.Server)

	// When requesting per-file atomic writes.
	result, err := fs.WriteMany(t.Context(), map[string][]byte{"/a": {1}, "/b": {2}}, &WriteManyOptions{Atomic: true})

	// Then per-file failures remain result data, not a whole-call sentinel.
	require.NoError(t, err)
	require.Equal(t, []BulkWriteFile{{Path: "/a", Bytes: 1}}, result.Files)
	require.Equal(t, []string{"/b: atomic replacement unsupported"}, result.Errors)
	require.Len(t, fake.requests(), 1)
	require.Equal(t, "true", fake.requests()[0].Query.Get("atomic"))
}
