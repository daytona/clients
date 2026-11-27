// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestProcessWait_whenRecordReadStallsIsBoundedLikeARound(t *testing.T) {
	for name, opts := range map[string]*WaitProcessOptions{"no deadline": nil, "ten minutes": {Timeout: 10 * time.Minute}} {
		t.Run(name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given a confirming metadata read that never responds, on a
				// client whose request timeout is off.
				mux := http.NewServeMux()
				mux.HandleFunc("GET /processes/p/wait", jsonHandler(t, map[string]string{"reason": "timed_out"}))
				mux.HandleFunc("GET /processes/p", func(_ http.ResponseWriter, r *http.Request) {
					<-r.Context().Done()
				})
				client := newTestClient(t, &httptest.Server{URL: "http://api.invalid"}, WithRequestTimeout(NoTimeout),
					WithHTTPClient(&http.Client{Transport: waitTestTransport{handler: mux.ServeHTTP}}))
				sandbox, err := client.newSandbox(sandboxInit{
					Info:    SandboxInfo{ID: "sbx"},
					Options: GetSandboxOptions{ToolboxURL: "http://toolbox.invalid", AccessToken: "fixture"},
				})
				require.NoError(t, err)
				handle, err := sandbox.Process().Connect("p")
				require.NoError(t, err)
				started := time.Now()

				// When waiting.
				_, err = handle.Wait(t.Context(), opts)

				// Then each read ends after one round (30 s + slack), never the
				// whole budget, and the bounded retry streak terminates.
				require.ErrorIs(t, err, ErrClientTimeout)
				require.Equal(t, 4*(processWaitRound+waitReadSlack), time.Since(started))
			})
		})
	}
}

func TestProcessWait_whenRecordReadStallsOnceStillReachesTheExit(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a timed_out round whose record read is dropped once.
		var mu sync.Mutex
		waits, reads := 0, 0
		mux := http.NewServeMux()
		mux.HandleFunc("GET /processes/p/wait", func(w http.ResponseWriter, _ *http.Request) {
			mu.Lock()
			waits++
			first := waits == 1
			mu.Unlock()
			if first {
				writeJSON(t, w, map[string]string{"reason": "timed_out"})
				return
			}
			writeJSON(t, w, map[string]any{"reason": "exited", "exitCode": 0})
		})
		mux.HandleFunc("GET /processes/p", func(_ http.ResponseWriter, r *http.Request) {
			mu.Lock()
			reads++
			mu.Unlock()
			<-r.Context().Done()
		})
		sandbox := dockerTimingSandbox(t, waitTestTransport{handler: mux.ServeHTTP}.RoundTrip)
		handle, err := sandbox.Process().Connect("p")
		require.NoError(t, err)
		started := time.Now()
		// When waiting with a ten-minute budget.
		result, err := handle.Wait(t.Context(), &WaitProcessOptions{Timeout: 10 * time.Minute})
		// Then the dropped read costs one round, and the wait reaches the exit.
		require.NoError(t, err)
		require.Equal(t, 0, *result.ExitCode)
		require.Equal(t, 1, reads)
		require.Equal(t, processWaitRound+waitReadSlack, time.Since(started))
	})
}
