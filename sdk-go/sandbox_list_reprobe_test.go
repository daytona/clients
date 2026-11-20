// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestList_whenRetiredFallbackReprobeFails_returnsSearchError(t *testing.T) {
	for _, status := range []int{http.StatusNotFound, http.StatusNotImplemented} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			// Given: the first call remembers fallback, then list is retired.
			var lists atomic.Int32
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/search": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(status)
					writeJSON(t, w, map[string]string{"code": CodeSearchNotConfigured})
				},
				"GET /sandboxes": func(w http.ResponseWriter, _ *http.Request) {
					if lists.Add(1) > 1 {
						w.Header().Set("Content-Type", "application/json")
						w.WriteHeader(http.StatusGone)
						writeJSON(t, w, map[string]string{"code": CodeGone})
						return
					}
					writeJSON(t, w, map[string]any{"sandboxes": []any{}})
				},
			})
			client := newTestClient(t, fake.Server)
			_, setupErr := client.List(t.Context(), nil)
			require.NoError(t, setupErr)

			// When
			_, err := client.List(t.Context(), nil)

			// Then
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, status, detail.StatusCode)
			require.Equal(t, CodeSearchNotConfigured, detail.Code)
			require.EqualValues(t, 2, lists.Load())
			require.Len(t, fake.requests(), 4)
			require.False(t, client.searchFallback.Load())
		})
	}
}
