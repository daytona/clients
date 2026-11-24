// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"io"
	"net/http"
	"path/filepath"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestFileReads_whenTimeoutInvalid_namesOptionBeforeNetwork(t *testing.T) {
	for _, method := range []string{"ReadFile", "ReadMany", "Download", "DownloadFile"} {
		t.Run(method, func(t *testing.T) {
			for _, timeout := range []time.Duration{WaitForever, NoWait, -time.Second} {
				t.Run(timeout.String(), func(t *testing.T) {
					// Given a timeout sentinel unsupported by file transfers.
					fake := newFakeServer(t, map[string]http.HandlerFunc{})
					fs := newFileTestService(t, fake.Server)
					destination := filepath.Join(t.TempDir(), "download")

					// When invoking any read/download entry point.
					var err error
					switch method {
					case "ReadFile":
						_, err = fs.ReadFile(t.Context(), "/a", &ReadFileOptions{Timeout: timeout})
					case "ReadMany":
						_, err = fs.ReadMany(t.Context(), []string{"/a"}, &ReadFileOptions{Timeout: timeout})
					case "Download":
						err = fs.Download(t.Context(), "/a", io.Discard, &DownloadOptions{Timeout: timeout})
					case "DownloadFile":
						err = fs.DownloadFile(t.Context(), "/a", destination, &DownloadOptions{Timeout: timeout})
					}

					// Then the local error identifies the public option without a request.
					require.ErrorIs(t, err, ErrBadRequest)
					var sdkErr *Error
					require.ErrorAs(t, err, &sdkErr)
					require.Equal(t, "SDK", sdkErr.Source)
					require.Contains(t, sdkErr.Message, "Timeout")
					require.Empty(t, fake.requests())
				})
			}
		})
	}
}
