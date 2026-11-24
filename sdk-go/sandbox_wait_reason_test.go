// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestWait_whenTerminalEventRefreshFails_preservesMatchingReadReason(t *testing.T) {
	for _, test := range []struct {
		name, status, eventStatus, eventReason, want string
	}{
		{"matching read", "failed", "failed", "", "disk full"},
		{"different raw state", "failed", "lost", "", ""},
		{"different state", "running", "failed", "", ""},
		{"event reason wins", "failed", "failed", "host lost", "host lost"},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given: a full read followed by a slim terminal event and an unavailable API.
			reads := 0
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx": func(w http.ResponseWriter, _ *http.Request) {
					reads++
					if reads > 1 {
						w.WriteHeader(http.StatusServiceUnavailable)
						return
					}
					info := sandboxFixture("sbx")
					info["status"], info["error"] = test.status, "disk full"
					writeJSON(t, w, info)
				},
			})
			client := newTestClient(t, fake.Server)
			sandbox, err := client.Get(t.Context(), "sbx", nil)
			require.NoError(t, err)
			require.Equal(t, "disk full", sandbox.Info().Error)
			source := &waitTestSource{events: make(chan stateObservation, 1)}
			source.events <- stateObservation{
				State: SandboxStateError, RawState: test.eventStatus, Reason: test.eventReason,
			}
			client.stateSource = source

			// When
			err = sandbox.Wait(t.Context(), nil)

			// Then: the reason describes only the matching state and wire status.
			var terminal *SandboxTerminalStateError
			require.ErrorAs(t, err, &terminal)
			require.Equal(t, test.eventStatus, terminal.RawState)
			require.Equal(t, test.want, terminal.Reason)
			require.Equal(t, test.want, sandbox.Info().Error)
		})
	}
}
