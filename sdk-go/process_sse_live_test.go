// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestStreamLogs_whenServerHoldsAfterLoneCR_deliversEvent(t *testing.T) {
	// Given: the server sends a complete CR-only event, then no more bytes.
	body := "event: log\rid: 1\rdata: {\"channel\":\"stdout\",\"data\":\"ok\",\"timestamp\":1}\r\r"
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p/logs": fileWatchHandler(t, body, true),
	})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	ctx, cancel := context.WithTimeout(t.Context(), time.Second)
	defer cancel()

	// When: following the public stream without releasing the server.
	var received ProcessLogEvent
	for event, streamErr := range handle.StreamLogs(ctx, nil) {
		require.NoError(t, streamErr)
		received = event
		break
	}

	// Then: the complete event arrives before cancellation or another byte.
	frame, ok := received.(ProcessLogFrameEvent)
	require.True(t, ok)
	require.Equal(t, "1", frame.Cursor)
	require.Equal(t, "ok", frame.Frame.Data)
}
