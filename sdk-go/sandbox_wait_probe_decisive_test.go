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

func TestWait_whenFrameArrivesDuringDecisiveProbe_settlesFromRead(t *testing.T) {
	for _, status := range []string{"running", "failed", "lost", "deleting", "deleted"} {
		t.Run(status, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given: a decisive GET from 10s to 12s overlaps a frame at 11s.
				began := time.Now()
				var reads []time.Duration
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
					reads = append(reads, time.Since(began))
					if len(reads) == 1 {
						timer := time.NewTimer(2 * time.Second)
						defer timer.Stop()
						<-timer.C
					}
					detail := sandboxFixture("sbx")
					detail["status"], detail["name"], detail["error"] = status, "from read", "read reason"
					writeJSON(t, w, detail)
				})
				source := &waitTestSource{
					events: make(chan stateObservation),
					lastFrame: func() time.Time {
						if time.Since(began) >= 11*time.Second {
							return began.Add(11 * time.Second)
						}
						return time.Time{}
					},
				}
				sandbox.client.stateSource = source

				// When
				err := sandbox.Wait(t.Context(), &WaitOptions{Timeout: 5 * time.Minute})

				// Then: settle at completion, with read-backed metadata and no confirming GET.
				require.Equal(t, []time.Duration{10 * time.Second}, reads)
				require.Equal(t, 12*time.Second, time.Since(began))
				if status == "running" {
					require.NoError(t, err)
				} else {
					var terminal *SandboxTerminalStateError
					require.ErrorAs(t, err, &terminal)
					require.Equal(t, status, terminal.RawState)
					require.Equal(t, "read reason", terminal.Reason)
				}
				require.Equal(t, sandboxState(status), sandbox.State())
				require.Equal(t, "from read", sandbox.Info().Name)
				require.EqualValues(t, 1, source.released.Load())
			})
		})
	}
}
