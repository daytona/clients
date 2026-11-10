// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build !windows

package toolbox

import (
	"io"
	"net/http"
	"os"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/stretchr/testify/require"
)

type review2ResizeTransport struct{ requests atomic.Int32 }

func (r *review2ResizeTransport) RoundTrip(*http.Request) (*http.Response, error) {
	r.requests.Add(1)
	return &http.Response{StatusCode: http.StatusNoContent, Body: io.NopCloser(strings.NewReader(""))}, nil
}

func TestForwardTerminalResizes_skipsGeometry_whenStdoutIsRedirected(t *testing.T) {
	// Given output redirected to a file rather than a terminal.
	output, err := os.CreateTemp(t.TempDir(), "stdout")
	require.NoError(t, err)
	defer output.Close()
	previous := os.Stdout
	os.Stdout = output
	defer func() { os.Stdout = previous }()
	// Observe attempted requests even if stop cancels before a real socket opens.
	transport := &review2ResizeTransport{}
	inner := api.New("https://toolbox.example", "fixture", "test")
	inner.HTTP.Transport = transport
	// When starting and stopping resize forwarding.
	stop := New(inner).forwardTerminalResizes(t.Context(), "proc")
	stop()
	// Then no fallback size changes the shared PTY.
	require.Zero(t, transport.requests.Load())
}
