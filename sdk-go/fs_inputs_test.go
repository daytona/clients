// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestFilePaths_whenRelative_failBeforeNetwork(t *testing.T) {
	calls := map[string]func(context.Context, *FileSystem) error{
		"stat":             func(ctx context.Context, f *FileSystem) error { _, err := f.Stat(ctx, "a"); return err },
		"list":             func(ctx context.Context, f *FileSystem) error { _, err := f.List(ctx, "a", nil); return err },
		"mkdir":            func(ctx context.Context, f *FileSystem) error { return f.Mkdir(ctx, "a", nil) },
		"remove":           func(ctx context.Context, f *FileSystem) error { return f.Remove(ctx, "a", nil) },
		"move source":      func(ctx context.Context, f *FileSystem) error { return f.Move(ctx, "a", "/b") },
		"move destination": func(ctx context.Context, f *FileSystem) error { return f.Move(ctx, "/a", "b") },
		"chmod":            func(ctx context.Context, f *FileSystem) error { return f.Chmod(ctx, "a", nil) },
		"grep":             func(ctx context.Context, f *FileSystem) error { _, err := f.Grep(ctx, "", "x"); return err },
		"glob":             func(ctx context.Context, f *FileSystem) error { _, err := f.Glob(ctx, ".", "*"); return err },
		"read":             func(ctx context.Context, f *FileSystem) error { _, err := f.ReadFile(ctx, "a", nil); return err },
		"write":            func(ctx context.Context, f *FileSystem) error { return f.WriteFile(ctx, "a", nil, nil) },
		"read many": func(ctx context.Context, f *FileSystem) error {
			_, err := f.ReadMany(ctx, []string{"/a", "b"}, nil)
			return err
		},
		"write many": func(ctx context.Context, f *FileSystem) error {
			_, err := f.WriteMany(ctx, map[string][]byte{"a": nil}, nil)
			return err
		},
		"upload":        func(ctx context.Context, f *FileSystem) error { return f.Upload(ctx, "a", strings.NewReader(""), nil) },
		"upload file":   func(ctx context.Context, f *FileSystem) error { return f.UploadFile(ctx, "missing", "a", nil) },
		"download":      func(ctx context.Context, f *FileSystem) error { return f.Download(ctx, "a", io.Discard, nil) },
		"download file": func(ctx context.Context, f *FileSystem) error { return f.DownloadFile(ctx, "a", "local", nil) },
		"replace": func(ctx context.Context, f *FileSystem) error {
			_, err := f.Replace(ctx, []string{"a"}, "x", "y")
			return err
		},
		"sign": func(ctx context.Context, f *FileSystem) error {
			_, err := f.PresignedUploadURL(ctx, "a", nil)
			return err
		},
		"watch": func(ctx context.Context, f *FileSystem) error { _, err := f.Watch(ctx, "a", nil); return err },
	}
	for name, call := range calls {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{})
			fs := newFileTestService(t, fake.Server)
			// When
			err := call(t.Context(), fs)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestFileReadOptions_whenInvalid_failBeforeNetwork(t *testing.T) {
	for _, opts := range []ReadFileOptions{
		{MaxResponseBytes: -2},
		{MaxResponseBytes: maxSafeInteger + 1},
		{Timeout: -time.Second},
		{Timeout: WaitForever},
	} {
		// Given
		fake := newFakeServer(t, map[string]http.HandlerFunc{})
		fs := newFileTestService(t, fake.Server)
		// When
		_, err := fs.ReadFile(t.Context(), "/a", &opts)
		// Then
		require.ErrorIs(t, err, ErrBadRequest)
		require.Empty(t, fake.requests())
	}
}
