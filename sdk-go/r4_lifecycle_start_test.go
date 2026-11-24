// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestStart_whenClosedAndOptionsInvalid_returnsClientClosed(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	sandbox, err := client.lifecycleSandbox("sbx")
	require.NoError(t, err)
	require.NoError(t, client.Close())
	// When
	err = sandbox.Start(t.Context(), &LifecycleWaitOptions{Timeout: -5 * time.Second})
	// Then
	require.ErrorIs(t, err, ErrClientClosed)
	require.Empty(t, fake.requests())
}

func TestStart_whenNoWaitObservesTerminalState_returnsTerminalError(t *testing.T) {
	for _, status := range []string{"deleting", "deleted", "failed", "lost"} {
		t.Run(status, func(t *testing.T) {
			// Given
			detail := sandboxFixture("sbx")
			detail["status"], detail["error"] = status, "observed failure"
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx": jsonHandler(t, detail),
			})
			client := newTestClient(t, fake.Server)
			sandbox, err := client.lifecycleSandbox("sbx")
			require.NoError(t, err)
			// When
			err = sandbox.Start(t.Context(), &LifecycleWaitOptions{Timeout: NoWait})
			// Then
			var terminal *SandboxTerminalStateError
			require.ErrorAs(t, err, &terminal)
			require.Equal(t, status, terminal.RawState)
			require.Equal(t, "observed failure", terminal.Reason)
			require.Len(t, fake.requests(), 1)
		})
	}
}
