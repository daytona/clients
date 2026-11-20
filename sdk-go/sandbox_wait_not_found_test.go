// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/require"
)

func TestWait_whenKnownNotFoundEvent_settlesWithoutRead(t *testing.T) {
	for _, test := range []struct {
		name     string
		target   SandboxState
		deleting bool
	}{
		{"wait", SandboxStateStarted, false},
		{"wait_destroyed", SandboxStateDestroyed, false},
		{"delete", SandboxStateDestroyed, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given: the real hub reports a known sandbox's terminal disappearance.
			t.Setenv("DAYTONA_SANDBOX_EVENTS", "1")
			finished := make(chan struct{})
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /events/region-a/ticket": jsonHandler(t, map[string]string{"ticket": "test-ticket"}),
				"GET /events/region-a": func(w http.ResponseWriter, r *http.Request) {
					defer close(finished)
					conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{"daytona.events.v1"}})
					if err != nil {
						t.Error(err)
						return
					}
					defer closeEventsTestSocket(t, conn)
					if _, _, err = conn.Read(r.Context()); err != nil {
						t.Error(err)
						return
					}
					data := []byte(`{"type":"error","id":"sbx-region-a","code":"NOT_FOUND","was_known":true}`)
					if err = conn.Write(r.Context(), websocket.MessageText, data); err != nil {
						t.Error(err)
						return
					}
					for {
						if _, _, readErr := conn.Read(r.Context()); readErr != nil {
							return
						}
					}
				},
				"DELETE /sandboxes/sbx-region-a": jsonHandler(t, struct{}{}),
				"GET /sandboxes/sbx-region-a": func(w http.ResponseWriter, _ *http.Request) {
					detail := sandboxFixture("sbx-region-a")
					if test.target == SandboxStateDestroyed {
						detail["status"] = "deleted"
					}
					writeJSON(t, w, detail)
				},
			})
			client := newEventsTestClient(t, fake.Server)
			t.Cleanup(func() {
				require.NoError(t, client.Close())
				<-finished
			})
			sandbox, err := client.lifecycleSandbox("sbx-region-a")
			require.NoError(t, err)
			sandbox.info.State, sandbox.info.RawState, sandbox.info.Error = SandboxStateStarting, "booting", "last reason"
			before := sandbox.Info()

			// When
			if test.deleting {
				err = sandbox.Delete(t.Context(), &LifecycleWaitOptions{Timeout: 10 * time.Second})
			} else {
				err = sandbox.Wait(t.Context(), &WaitOptions{Until: []SandboxState{test.target}, Timeout: 10 * time.Second})
			}

			// Then: a terminal disappearance needs no GET or synthetic unknown state.
			if test.deleting {
				require.NoError(t, err)
				require.Equal(t, SandboxStateDestroyed, sandbox.State())
				require.Equal(t, "deleted", sandbox.Info().RawState)
				require.Empty(t, sandbox.Info().Error)
			} else {
				require.ErrorIs(t, err, ErrNotFound)
				var detail *Error
				require.ErrorAs(t, err, &detail)
				require.Equal(t, SourceSDK, detail.Source)
				require.Equal(t, http.StatusNotFound, detail.StatusCode)
				require.Equal(t, before, sandbox.Info())
			}
			for _, request := range fake.requests() {
				require.NotEqual(t, "GET /sandboxes/sbx-region-a", request.Method+" "+request.Path)
			}
		})
	}
}

func TestWait_whenKnownNotFoundArrivesDuringProbe_cancelsAndJoinsRead(t *testing.T) {
	for name, deleting := range map[string]bool{"wait": false, "delete": true} {
		t.Run(name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given: a terminal disappearance arrives while the first GET is held.
				source := &waitTestSource{events: make(chan stateObservation, 1)}
				reads := 0
				joined := false
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
					if r.Method == http.MethodDelete {
						writeJSON(t, w, struct{}{})
						return
					}
					reads++
					if reads == 1 {
						source.events <- stateObservation{NotFound: true}
						close(source.events)
						<-r.Context().Done()
						joined = true
						return
					}
					detail := sandboxFixture("sbx")
					if deleting {
						detail["status"] = "deleted"
					}
					writeJSON(t, w, detail)
				})
				sandbox.client.stateSource = source
				sandbox.info.State, sandbox.info.RawState = SandboxStateStarting, "booting"
				before := sandbox.Info()

				// When
				var err error
				if deleting {
					err = sandbox.Delete(t.Context(), &LifecycleWaitOptions{Timeout: 8 * time.Second})
				} else {
					err = sandbox.Wait(t.Context(), &WaitOptions{Timeout: 8 * time.Second})
				}

				// Then: the in-flight read is joined, with no confirming or fallback GET.
				if deleting {
					require.NoError(t, err)
					require.Equal(t, SandboxStateDestroyed, sandbox.State())
					require.Equal(t, "deleted", sandbox.Info().RawState)
				} else {
					require.ErrorIs(t, err, ErrNotFound)
					var detail *Error
					require.ErrorAs(t, err, &detail)
					require.Equal(t, SourceSDK, detail.Source)
					require.Equal(t, before, sandbox.Info())
				}
				require.True(t, joined)
				require.Equal(t, 1, reads)
				require.EqualValues(t, 1, source.released.Load())
			})
		})
	}
}
