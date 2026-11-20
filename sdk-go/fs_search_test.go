// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFileSearch_whenNamesAndContentsDiffer(t *testing.T) {
	t.Run("glob names", func(t *testing.T) {
		// Given
		fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/search": jsonHandler(t,
			map[string]any{"files": []string{"/root/a.csv"}, "skipped": []string{"/root/private"}})})
		fs := newFileTestService(t, fake.Server)
		// When
		result, err := fs.Glob(t.Context(), "/root", "*.csv")
		// Then
		require.NoError(t, err)
		require.Equal(t, []string{"/root/a.csv"}, result.Items)
		require.Equal(t, []string{"/root/private"}, result.Skipped)
		require.Equal(t, "*.csv", fake.requests()[0].Query.Get("pattern"))
	})
	for _, legacy := range []bool{true, false} {
		t.Run(map[bool]string{true: "grep legacy", false: "grep envelope"}[legacy], func(t *testing.T) {
			// Given
			var payload any = []any{map[string]any{"file": "/a", "line": 3, "content": "x.y"}}
			if !legacy {
				payload = map[string]any{"items": payload, "skipped": []string{"/private"}}
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/find": jsonHandler(t, payload)})
			fs := newFileTestService(t, fake.Server)
			// When
			result, err := fs.Grep(t.Context(), "/", "x.y")
			// Then
			require.NoError(t, err)
			require.Equal(t, []FileMatch{{File: "/a", Line: 3, Content: "x.y"}}, result.Items)
			if legacy {
				require.Empty(t, result.Skipped)
			} else {
				require.Equal(t, []string{"/private"}, result.Skipped)
			}
			require.Equal(t, "x.y", fake.requests()[0].Query.Get("pattern"))
		})
	}
}

func TestFileReplace_whenOneFileFails(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /files/replace": jsonHandler(t,
		[]any{map[string]any{"file": "/a", "success": false, "error": "access denied"}})})
	fs := newFileTestService(t, fake.Server)
	// When
	result, err := fs.Replace(t.Context(), []string{"/a"}, "old", "new")
	// Then
	require.NoError(t, err)
	require.Len(t, result.Items, 1)
	require.False(t, result.Items[0].Success)
	require.Equal(t, "access denied", *result.Items[0].Error)
	require.JSONEq(t, `{"files":["/a"],"pattern":"old","newValue":"new"}`, string(fake.requests()[0].Body))
}

func TestFileSearch_whenMalformedResponse(t *testing.T) {
	for _, payload := range []string{
		`{"items":null}`, `{"items":[{"file":"/a","line":-1,"content":"x"}]}`,
		`{"items":[{"file":"/a","line":1.5,"content":"x"}]}`,
		`{"items":[],"skipped":[null]}`, `{"items":[],"skipped":"private"}`,
	} {
		t.Run(payload, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/find": func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if _, err := w.Write([]byte(payload)); err != nil {
					t.Error(err)
				}
			}})
			fs := newFileTestService(t, fake.Server)
			// When
			_, err := fs.Grep(t.Context(), "/", "x")
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
