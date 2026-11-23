// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

type transferCubicCloseBody struct {
	io.ReadCloser
	failure error
	closes  int
}

func (b *transferCubicCloseBody) Close() error {
	b.closes++
	return errors.Join(b.ReadCloser.Close(), b.failure)
}

type transferCubicResponseTransport struct {
	next http.RoundTripper
	body *transferCubicCloseBody
}

func (t transferCubicResponseTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	response, err := t.next.RoundTrip(request)
	if err == nil {
		t.body.ReadCloser = response.Body
		response.Body = t.body
	}
	return response, err
}

func TestDownloadFile_whenResponseCloseFails_preservesDestination(t *testing.T) {
	for _, existing := range []bool{false, true} {
		t.Run(map[bool]string{false: "new destination", true: "existing destination"}[existing], func(t *testing.T) {
			// Given a complete body whose close fails and a destination to protect.
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /files/download": func(w http.ResponseWriter, _ *http.Request) {
				if _, err := io.WriteString(w, "replacement"); err != nil {
					t.Error(err)
				}
			}})
			closeFailure := errors.New("response close failed")
			body := &transferCubicCloseBody{failure: closeFailure}
			client := fake.Server.Client()
			client.Transport = transferCubicResponseTransport{
				next: client.Transport,
				body: body,
			}
			fs := newFileTestService(t, fake.Server, WithHTTPClient(client))
			directory := t.TempDir()
			destination := filepath.Join(directory, "result")
			if existing {
				require.NoError(t, os.WriteFile(destination, []byte("original"), 0o600))
			}

			// When downloading through the staging file.
			err := fs.DownloadFile(t.Context(), "/a", destination, nil)

			// Then the close error is reported before any destination replacement.
			require.Error(t, err)
			require.Equal(t, 1, body.closes)
			if existing {
				data, readErr := os.ReadFile(destination)
				require.NoError(t, readErr)
				require.Equal(t, "original", string(data))
			} else {
				_, statErr := os.Stat(destination)
				require.ErrorIs(t, statErr, os.ErrNotExist)
			}
			staged, globErr := filepath.Glob(filepath.Join(directory, ".daytona-dl-*"))
			require.NoError(t, globErr)
			require.Empty(t, staged)
		})
	}
}
