// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestLifecycle_whenAccepted_waitsForTarget(t *testing.T) {
	for _, test := range []struct {
		action, status, query string
		call                  func(context.Context, *Sandbox) error
	}{
		{"stop", "stopped", "wait", func(ctx context.Context, s *Sandbox) error { return s.Stop(ctx, nil) }},
		{"pause", "paused", "wait", func(ctx context.Context, s *Sandbox) error { return s.Pause(ctx, nil) }},
		{"resume", "running", "wait", func(ctx context.Context, s *Sandbox) error { return s.Resume(ctx, nil) }},
		{"", "deleted", "wait_exact", func(ctx context.Context, s *Sandbox) error { return s.Delete(ctx, nil) }},
	} {
		t.Run(test.action, func(t *testing.T) {
			// Given: class/persistence never prevent a lifecycle request.
			detail := sandboxFixture("sbx")
			detail["status"], detail["class"], detail["persistent"] = test.status, "future", false
			route := "POST /sandboxes/sbx/" + test.action
			if test.action == "" {
				route = "DELETE /sandboxes/sbx"
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				route:                jsonHandler(t, map[string]string{}),
				"GET /sandboxes/sbx": jsonHandler(t, detail),
			})
			sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
			require.NoError(t, err)
			// When
			err = test.call(t.Context(), sandbox)
			// Then
			require.NoError(t, err)
			require.Equal(t, sandboxState(test.status), sandbox.State())
			requests := fake.requests()
			require.Len(t, requests, 2)
			require.Equal(t, test.status, requests[1].Query.Get(test.query))
			require.Equal(t, "30000", requests[1].Query.Get("timeout_ms"))
		})
	}
}

func TestLifecycleByID_whenNoWait_sendsOnlyAction(t *testing.T) {
	for _, verb := range []string{"stop", "pause", "resume", "delete"} {
		t.Run(verb, func(t *testing.T) {
			// Given
			path, method := "/sandboxes/a%2Fb/"+verb, http.MethodPost
			if verb == "delete" {
				path, method = "/sandboxes/a%2Fb", http.MethodDelete
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				method + " " + path: jsonHandler(t, map[string]string{}),
			})
			client := newTestClient(t, fake.Server)
			calls := map[string]func(context.Context, string, *LifecycleWaitOptions) error{
				"stop": client.Stop, "pause": client.Pause, "resume": client.Resume, "delete": client.Delete,
			}
			// When
			err := calls[verb](t.Context(), "a/b", &LifecycleWaitOptions{Timeout: NoWait})
			// Then
			require.NoError(t, err)
			require.Len(t, fake.requests(), 1)
			require.Equal(t, path, fake.requests()[0].Path)
		})
	}
}

func TestLifecycle_whenInvalid_rejectsBeforeRequests(t *testing.T) {
	for _, id := range []string{"", "..", "sbx"} {
		t.Run(id, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			for _, call := range []func(context.Context, string, *LifecycleWaitOptions) error{
				client.Start, client.Stop, client.Pause, client.Resume, client.Delete,
			} {
				// When
				err := call(t.Context(), id, &LifecycleWaitOptions{Timeout: NoTimeout})
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
			}
			require.Empty(t, fake.requests())
		})
	}
}

func TestDelete_whenWaitReturnsNotFound_completes(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"DELETE /sandboxes/sbx": jsonHandler(t, map[string]string{}),
		"GET /sandboxes/sbx": func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusNotFound)
		},
	})
	sandbox, err := newTestClient(t, fake.Server).lifecycleSandbox("sbx")
	require.NoError(t, err)
	// When
	err = sandbox.Delete(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, SandboxStateDestroyed, sandbox.State())
	require.Equal(t, "deleted", sandbox.Info().RawState)
}

func TestStart_whenStoppingOrPausing_settlesBeforeRouting(t *testing.T) {
	for _, test := range []struct{ before, settled, action string }{
		{"stopping", "stopped", "start"},
		{"pausing", "paused", "resume"},
		{"stopping", "running", ""},
		{"paused", "paused", "resume"},
		{"stopped", "stopped", "start"},
	} {
		t.Run(test.before+"-"+test.settled, func(t *testing.T) {
			// Given
			var reads atomic.Int32
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx": func(w http.ResponseWriter, _ *http.Request) {
					detail := sandboxFixture("sbx")
					switch reads.Add(1) {
					case 1:
						detail["status"] = test.before
					case 2:
						if test.before == "stopping" || test.before == "pausing" {
							detail["status"] = test.settled
						}
					}
					writeJSON(t, w, detail)
				},
				"POST /sandboxes/sbx/" + test.action: jsonHandler(t, map[string]string{}),
			})
			client := newTestClient(t, fake.Server)
			// When
			err := client.Start(t.Context(), "sbx", nil)
			// Then
			require.NoError(t, err)
			var actions []string
			for _, request := range fake.requests() {
				if request.Method == http.MethodPost {
					actions = append(actions, request.Path)
				}
			}
			if test.action == "" {
				require.Empty(t, actions)
			} else {
				require.Equal(t, []string{"/sandboxes/sbx/" + test.action}, actions)
			}
		})
	}
}

func TestLifecycle_whenTransitionFails_preservesRawReason(t *testing.T) {
	for _, test := range []struct{ verb, status string }{
		{"stop", "failed"}, {"pause", "running"}, {"resume", "stopped"}, {"delete", "lost"},
	} {
		t.Run(test.verb, func(t *testing.T) {
			// Given
			detail := sandboxFixture("sbx")
			detail["status"], detail["error"] = test.status, "transition failed"
			route := "POST /sandboxes/sbx/" + test.verb
			if test.verb == "delete" {
				route = "DELETE /sandboxes/sbx"
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				route: jsonHandler(t, map[string]string{}), "GET /sandboxes/sbx": jsonHandler(t, detail),
			})
			client := newTestClient(t, fake.Server)
			call := map[string]func(context.Context, string, *LifecycleWaitOptions) error{
				"stop": client.Stop, "pause": client.Pause, "resume": client.Resume, "delete": client.Delete,
			}[test.verb]
			// When
			err := call(t.Context(), "sbx", nil)
			// Then
			var terminal *SandboxTerminalStateError
			require.ErrorAs(t, err, &terminal)
			require.Equal(t, test.status, terminal.RawState)
			require.Equal(t, "transition failed", terminal.Reason)
			require.ErrorIs(t, err, ErrConflict)
		})
	}
}
