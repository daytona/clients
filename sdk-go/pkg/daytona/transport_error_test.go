// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	stderrors "errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	sdkerrors "github.com/daytona/clients/sdk-go/pkg/errors"
	"github.com/stretchr/testify/require"
)

// newDroppingServer returns a server that accepts each request and closes the
// connection without writing a response, so the client sees a transport error.
func newDroppingServer(t *testing.T) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			// Handler goroutine: t.Errorf, not require (FailNow must run on the test goroutine).
			t.Errorf("hijack failed: %v", err)
			return
		}
		_ = conn.Close()
	}))
	t.Cleanup(server.Close)
	return server
}

// requireTransportCause asserts the SDK error is a *DaytonaError with no status
// whose original *url.Error is still reachable through errors.As.
func requireTransportCause(t *testing.T, err error) {
	t.Helper()
	require.Error(t, err)

	var de *sdkerrors.DaytonaError
	require.True(t, stderrors.As(err, &de), "want *DaytonaError, got %T", err)
	require.Zero(t, de.StatusCode)

	var urlErr *url.Error
	require.True(t, stderrors.As(err, &urlErr), "original *url.Error must be reachable, got chain %v", err)
}

func TestTransportErrors_KeepOriginalCause(t *testing.T) {
	fs := NewFileSystemService(createTestToolboxClient(newDroppingServer(t)), nil)
	ctx := context.Background()

	t.Run("generated client (fromGenericError)", func(t *testing.T) {
		requireTransportCause(t, fs.CreateFolder(ctx, "/home/user/dir"))
	})

	t.Run("download stream", func(t *testing.T) {
		stream, err := fs.DownloadFileStream(ctx, "/home/user/file.txt")
		if stream != nil {
			_ = stream.Close()
		}
		requireTransportCause(t, err)
	})

	t.Run("upload stream", func(t *testing.T) {
		requireTransportCause(t, fs.UploadFileStream(ctx, strings.NewReader("data"), "/home/user/file.txt"))
	})
}

func TestTransportErrors_ContextDeadlineIsDetectable(t *testing.T) {
	block := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-block:
		case <-r.Context().Done():
		}
	}))
	t.Cleanup(server.Close)
	t.Cleanup(func() { close(block) })

	fs := NewFileSystemService(createTestToolboxClient(server), nil)
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	_, err := fs.DownloadFileStream(ctx, "/home/user/file.txt")
	require.Error(t, err)
	require.True(t, stderrors.Is(err, context.DeadlineExceeded), "got %v", err)
}
