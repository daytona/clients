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

func TestFileWatch_whenServerHoldsAfterLoneCR_returnsHandshake(t *testing.T) {
	// Given: a complete CR-only handshake with the response held open.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /files/watch": fileWatchHandler(t, "event: start\rdata: {}\r\r", true),
	})
	fs := newFileTestService(t, fake.Server)
	ctx, cancel := context.WithTimeout(t.Context(), time.Second)
	defer cancel()

	// When
	watch, err := fs.Watch(ctx, "/", nil)

	// Then: no subsequent event is needed to complete the handshake.
	require.NoError(t, err)
	watch.Stop()
	require.NoError(t, watch.Wait(ctx))
}
