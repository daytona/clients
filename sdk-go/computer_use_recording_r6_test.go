// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"syscall"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestComputerUseRecordingDownload_whenCloseFailsReturnsNil(t *testing.T) {
	// Given a complete recording body that fails on close.
	client := newTestClient(t, &httptest.Server{URL: "http://recording.example"},
		WithHTTPClient(&http.Client{Transport: buildLogsRoundTrip(func(*http.Request) (*http.Response, error) {
			return &http.Response{
				StatusCode: http.StatusOK, Header: make(http.Header),
				Body: responseCloseFailure{strings.NewReader("recording")},
			}, nil
		})}))
	sandbox, setupErr := client.newSandbox(sandboxInit{
		Info: SandboxInfo{ID: "sbx"}, Options: GetSandboxOptions{
			ToolboxURL: "http://recording.example", AccessToken: "fixture-token",
		},
	})
	require.NoError(t, setupErr)
	// When the recording is downloaded.
	data, err := sandbox.ComputerUse().Recordings().Download(t.Context(), "recording", nil)
	// Then the close failure is preserved without returning successful data.
	require.ErrorIs(t, err, syscall.EIO)
	require.Nil(t, data)
}
