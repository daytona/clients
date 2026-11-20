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

func TestProcessWait_whenUnboundedRecordReadStallsUsesRequestTimeout(t *testing.T) {
	// Given an unbounded wait whose confirming metadata read never responds.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /processes/p/wait": jsonHandler(t, map[string]string{"reason": "timed_out"}),
		"GET /processes/p": func(_ http.ResponseWriter, r *http.Request) {
			<-r.Context().Done()
		},
	})
	client := newTestClient(t, fake.Server, WithRequestTimeout(50*time.Millisecond))
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetSandboxOptions{ToolboxURL: fake.Server.URL, AccessToken: "fixture"},
	})
	require.NoError(t, err)
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	ctx, cancel := context.WithTimeout(t.Context(), 2*time.Second)
	defer cancel()

	// When waiting without an SDK wait budget.
	_, err = handle.Wait(ctx, nil)

	// Then each ordinary read times out and the bounded retry streak terminates.
	require.ErrorIs(t, err, ErrClientTimeout)
	require.NoError(t, ctx.Err())
	require.Len(t, fake.requests(), 8)
}
