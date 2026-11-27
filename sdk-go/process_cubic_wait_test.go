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

func TestProcessWait_whenBudgetExpires_readsTheRecordOnce(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given the long poll consumes the entire observation budget, and the
		// process ended with it.
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
		// When the wait budget expires with that round.
		result, err := handle.Wait(t.Context(), &WaitProcessOptions{Timeout: time.Second})
		// Then one record read reports the exit instead of timed_out, as in
		// Python and TypeScript.
		require.NoError(t, err)
		require.Equal(t, ProcessReasonExited, result.Reason)
		require.Equal(t, 7, *result.ExitCode)
		require.Equal(t, 1, reads)
		require.Equal(t, time.Second, time.Since(started))
	})
}

func TestProcessWait_whenBudgetExpiresOnARunningProcess_returnsTimedOut(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		mux := http.NewServeMux()
		mux.HandleFunc("GET /processes/p/wait", func(w http.ResponseWriter, _ *http.Request) {
			<-time.After(time.Second)
			writeJSON(t, w, map[string]string{"reason": "timed_out"})
		})
		mux.HandleFunc("GET /processes/p", jsonHandler(t, processFixture("p")))
		sandbox := dockerTimingSandbox(t, waitTestTransport{handler: mux.ServeHTTP}.RoundTrip)
		handle, err := sandbox.Process().Connect("p")
		require.NoError(t, err)
		result, err := handle.Wait(t.Context(), &WaitProcessOptions{Timeout: time.Second})
		require.NoError(t, err)
		require.Equal(t, ProcessReasonTimedOut, result.Reason)
		require.Nil(t, result.ExitCode)
	})
}
