// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestStart_whenReadConsumesBudget_sendsNoAction(t *testing.T) {
	for _, state := range []string{"stopped", "paused"} {
		t.Run(state, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given: the response is readable, but parsing crosses the SDK deadline.
				posts := 0
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
					if r.Method == http.MethodPost {
						posts++
						w.WriteHeader(http.StatusNoContent)
						return
					}
					detail := sandboxFixture("sbx")
					detail["status"] = state
					writeJSON(t, w, detail)
				})
				wait, err := sandbox.newWait([]SandboxState{SandboxStateStarted})
				require.NoError(t, err)
				wait.deadline = time.Now().Add(-time.Second)
				sandbox.info.State = sandboxState(state)
				// When: exercise the post-read action with an already-spent shared budget.
				err = sandbox.startObserved(t.Context(), wait, false)
				// Then
				require.ErrorIs(t, err, ErrRequestTimeout)
				require.Zero(t, posts)
			})
		})
	}
}

func TestWait_whenClosedAndOptionsInvalid_returnsClientClosed(t *testing.T) {
	for _, opts := range []*WaitOptions{
		{Timeout: -time.Second}, {Until: []SandboxState{}}, {Until: []SandboxState{"invalid"}},
	} {
		// Given
		fake := newFakeServer(t, nil)
		sandbox := newMutatorSandbox(t, fake)
		require.NoError(t, sandbox.client.Close())
		// When
		err := sandbox.Wait(t.Context(), opts)
		// Then
		require.ErrorIs(t, err, ErrClientClosed)
		require.Empty(t, fake.requests())
	}
}
