// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

// Remove, Grep, Glob, Replace and Chmod take a per-call Timeout that replaces
// the client's request timeout for that call. The Python and TypeScript
// suites assert the same behaviour.

type fsTimedCall struct {
	name, method, path, body string
	call                     func(context.Context, *FileSystem, time.Duration) error
}

var fsTimedCalls = []fsTimedCall{
	{"Remove", http.MethodDelete, "/files", "", func(ctx context.Context, f *FileSystem, d time.Duration) error {
		return f.Remove(ctx, "/tree", &RemoveFileOptions{Recursive: true, Timeout: d})
	}},
	{"Grep", http.MethodGet, "/files/find", `{"items":[]}`, func(ctx context.Context, f *FileSystem, d time.Duration) error {
		_, err := f.Grep(ctx, "/tree", "needle", &FileSearchOptions{Timeout: d})
		return err
	}},
	{"Glob", http.MethodGet, "/files/search", `{"files":[]}`, func(ctx context.Context, f *FileSystem, d time.Duration) error {
		_, err := f.Glob(ctx, "/tree", "*.go", &FileSearchOptions{Timeout: d})
		return err
	}},
	{"Replace", http.MethodPost, "/files/replace", `{"items":[]}`, func(ctx context.Context, f *FileSystem, d time.Duration) error {
		_, err := f.Replace(ctx, []string{"/tree/a"}, "old", "new", &ReplaceOptions{Timeout: d})
		return err
	}},
	{"Chmod", http.MethodPost, "/files/permissions", "", func(ctx context.Context, f *FileSystem, d time.Duration) error {
		return f.Chmod(ctx, "/tree", &FilePermissionsOptions{Mode: "0755", Timeout: d})
	}},
}

func fsTimedSandbox(t *testing.T, call fsTimedCall, delay, requestTimeout time.Duration) (*Sandbox, *atomic.Int32) {
	t.Helper()
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != call.method || r.URL.Path != call.path {
			http.NotFound(w, r)
			return
		}
		requests.Add(1)
		select {
		case <-time.After(delay):
			if call.body != "" {
				w.Header().Set("Content-Type", "application/json")
			}
			if _, err := w.Write([]byte(call.body)); err != nil {
				t.Errorf("write: %v", err)
			}
		case <-r.Context().Done():
		}
	}))
	t.Cleanup(server.Close)
	client := newTestClient(t, server, WithRequestTimeout(requestTimeout))
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetSandboxOptions{ToolboxURL: server.URL, AccessToken: strings.Join([]string{"test", "sandbox"}, "-")},
	})
	require.NoError(t, err)
	return sandbox, &requests
}

func TestFileSystemCallTimeouts(t *testing.T) {
	for _, call := range fsTimedCalls {
		t.Run(call.name+" zero keeps the request timeout", func(t *testing.T) {
			sandbox, requests := fsTimedSandbox(t, call, 400*time.Millisecond, 100*time.Millisecond)
			require.ErrorIs(t, call.call(t.Context(), sandbox.FS(), 0), ErrClientTimeout)
			require.Equal(t, int32(1), requests.Load())
		})
		t.Run(call.name+" outlasts a shorter request timeout", func(t *testing.T) {
			// The call answers after 0.4 s, past the 0.1 s request timeout: a
			// longer per-call timeout, NoTimeout and one above the timer maximum
			// let it finish.
			for _, timeout := range []time.Duration{2 * time.Second, NoTimeout, maxTimerDuration + time.Second} {
				sandbox, requests := fsTimedSandbox(t, call, 400*time.Millisecond, 100*time.Millisecond)
				require.NoError(t, call.call(t.Context(), sandbox.FS(), timeout))
				require.Equal(t, int32(1), requests.Load())
			}
		})
		t.Run(call.name+" shorter timeout ends the call", func(t *testing.T) {
			sandbox, requests := fsTimedSandbox(t, call, 400*time.Millisecond, 30*time.Second)
			require.ErrorIs(t, call.call(t.Context(), sandbox.FS(), 100*time.Millisecond), ErrClientTimeout)
			require.Equal(t, int32(1), requests.Load())
		})
		t.Run(call.name+" refuses invalid timeouts before any request", func(t *testing.T) {
			sandbox, requests := fsTimedSandbox(t, call, 0, 30*time.Second)
			for _, timeout := range []time.Duration{-time.Second, WaitForever, NoWait} {
				require.ErrorIs(t, call.call(t.Context(), sandbox.FS(), timeout), ErrBadRequest)
			}
			require.Equal(t, int32(0), requests.Load())
		})
	}
}

func TestFileSystemSearchTakesAtMostOneOptions(t *testing.T) {
	sandbox, requests := fsTimedSandbox(t, fsTimedCalls[1], 0, 30*time.Second)
	fs := sandbox.FS()
	_, err := fs.Grep(t.Context(), "/tree", "x", &FileSearchOptions{}, &FileSearchOptions{})
	require.ErrorIs(t, err, ErrBadRequest)
	_, err = fs.Glob(t.Context(), "/tree", "x", nil, nil)
	require.ErrorIs(t, err, ErrBadRequest)
	_, err = fs.Replace(t.Context(), []string{"/a"}, "x", "y", &ReplaceOptions{}, &ReplaceOptions{})
	require.ErrorIs(t, err, ErrBadRequest)
	require.Equal(t, int32(0), requests.Load())
	// Nil and absent options keep the request timeout.
	_, err = fs.Grep(t.Context(), "/tree", "x", nil)
	require.NoError(t, err)
	_, err = fs.Grep(t.Context(), "/tree", "x")
	require.NoError(t, err)
}
