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

func TestProcessWait_whenBudgetExpires_doesNotStartRecordRead(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given the long poll consumes the entire observation budget.
		reads := 0
		mux := http.NewServeMux()
		mux.HandleFunc("GET /processes/p/wait", func(w http.ResponseWriter, _ *http.Request) {
			<-time.After(time.Second)
			writeJSON(t, w, map[string]string{"reason": "timed_out"})
		})
		mux.HandleFunc("GET /processes/p", func(w http.ResponseWriter, _ *http.Request) {
			reads++
			fixture := processFixture("p")
			fixture["state"], fixture["reason"], fixture["exitCode"] = "terminal", "exited", 7
			writeJSON(t, w, fixture)
		})
		sandbox := dockerTimingSandbox(t, waitTestTransport{handler: mux.ServeHTTP}.RoundTrip)
		handle, err := sandbox.Process().Connect("p")
		require.NoError(t, err)
		started := time.Now()
		// When the wait budget expires, even if a later read could see terminal.
		result, err := handle.Wait(t.Context(), &WaitProcessOptions{Timeout: time.Second})
		// Then match TypeScript: report the expired observation without another request.
		require.NoError(t, err)
		require.Equal(t, ProcessReasonTimedOut, result.Reason)
		require.Nil(t, result.ExitCode)
		require.Zero(t, reads)
		require.Equal(t, time.Second, time.Since(started))
	})
}
