// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestReadFile_whenCapSelected(t *testing.T) {
	for _, tc := range []struct {
		name      string
		limit     int64
		wantError bool
	}{{"default", 0, true}, {"lower", 1, true}, {"higher", 8, false}, {"disabled", NoLimit, false}} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/download": func(w http.ResponseWriter, _ *http.Request) {
				if _, err := io.WriteString(w, "payload"); err != nil {
					t.Error(err)
				}
			}})
			fs := newFileTestService(t, fake.Server, WithMaxResponseBytes(3))
			// When
			data, err := fs.ReadFile(t.Context(), "/a", &ReadFileOptions{MaxResponseBytes: tc.limit})
			// Then
			if tc.wantError {
				require.ErrorIs(t, err, ErrPayloadTooLarge)
				return
			}
			require.NoError(t, err)
			require.Equal(t, "payload", string(data))
		})
	}
}

func TestReadText_whenStrictUTF8(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		valid      bool
	}{
		{"bom", "\ufefftext", true}, {"invalid", "\xff", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/download": func(w http.ResponseWriter, _ *http.Request) {
				if _, err := io.WriteString(w, tc.body); err != nil {
					t.Error(err)
				}
			}})
			fs := newFileTestService(t, fake.Server)
			// When
			text, err := fs.ReadText(t.Context(), "/a", nil)
			// Then
			if tc.valid {
				require.NoError(t, err)
				require.Equal(t, tc.body, text)
			} else {
				require.ErrorIs(t, err, ErrBadRequest)
			}
		})
	}
}

func TestFileTransfer_whenIdle_returnsTimeout(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/download": func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		if err := http.NewResponseController(w).Flush(); err != nil {
			t.Error(err)
			return
		}
		<-r.Context().Done()
	}})
	fs := newFileTestService(t, fake.Server, WithRequestTimeout(time.Minute))
	// When
	_, err := fs.ReadFile(t.Context(), "/a", &ReadFileOptions{Timeout: 25 * time.Millisecond})
	// Then
	require.ErrorIs(t, err, ErrClientTimeout)
	require.ErrorIs(t, err, context.DeadlineExceeded)
}

func TestFileRead_whenChunkedExceedsCap(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/download": func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(200)
		if err := http.NewResponseController(w).Flush(); err != nil {
			t.Error(err)
			return
		}
		if _, err := io.WriteString(w, "0123456789"); err != nil {
			t.Error(err)
		}
	}})
	fs := newFileTestService(t, fake.Server)
	// When
	_, err := fs.ReadFile(t.Context(), "/a", &ReadFileOptions{MaxResponseBytes: 3})
	// Then
	require.ErrorIs(t, err, ErrPayloadTooLarge)
}

func TestFileRead_whenOversizedError_preservesHTTPStatus(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/download": func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		writeJSON(t, w, map[string]string{"message": "forbidden", "source": "TOOLBOX", "code": "FILE_ACCESS_DENIED"})
	}})
	fs := newFileTestService(t, fake.Server)
	// When
	_, err := fs.ReadFile(t.Context(), "/a", &ReadFileOptions{MaxResponseBytes: 1})
	// Then
	require.ErrorIs(t, err, ErrForbidden)
	require.NotErrorIs(t, err, ErrPayloadTooLarge)
}
