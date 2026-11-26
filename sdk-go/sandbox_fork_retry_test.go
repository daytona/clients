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

func TestFork_whenRetryChangesSourceState(t *testing.T) {
	for _, tc := range []struct{ initial, settled string }{
		{"starting", "running"},
		{"pausing", "paused"},
		{"stopping", "stopped"},
	} {
		t.Run(tc.initial, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given a source that settles while its fork submission retries.
				posts, sourceReads := 0, 0
				source := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
					switch r.URL.Path {
					case "/sandboxes/sbx":
						sourceReads++
						detail := sandboxFixture("sbx")
						detail["status"] = tc.initial
						if posts > 1 {
							detail["status"] = tc.settled
						}
						writeJSON(t, w, detail)
					case "/sandboxes/sbx/fork":
						posts++
						if posts == 1 {
							w.Header().Set("Content-Type", "application/json")
							w.WriteHeader(http.StatusConflict)
							writeJSON(t, w, map[string]string{"code": CodeSandboxTransitionInProgress})
							return
						}
						writeJSON(t, w, map[string]string{"id": "fork", "status": "pending"})
					case "/sandboxes/fork":
						detail := sandboxFixture("fork")
						detail["status"] = tc.settled
						writeJSON(t, w, detail)
					default:
						t.Errorf("unexpected request: %s", r.URL.Path)
					}
				})
				started := time.Now()
				// When forking with a readiness budget longer than the retry.
				fork, err := source.Fork(t.Context(), &ForkOptions{Wait: 10 * time.Second})
				// Then readiness completes immediately in the newly settled state.
				require.NoError(t, err)
				require.Equal(t, sandboxState(tc.settled), fork.State())
				require.Equal(t, time.Second, time.Since(started))
				require.Equal(t, 2, sourceReads)
			})
		})
	}
}
