// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func transitionMethodCases() map[string]func(context.Context, *Sandbox) error {
	return map[string]func(context.Context, *Sandbox) error{
		"fork": func(ctx context.Context, sandbox *Sandbox) error {
			_, err := sandbox.Fork(ctx, &ForkOptions{Wait: NoWait})
			return err
		},
		"images": func(ctx context.Context, sandbox *Sandbox) error {
			_, err := sandbox.CaptureImage(ctx, nil)
			return err
		},
		"snapshots": func(ctx context.Context, sandbox *Sandbox) error {
			_, err := sandbox.CreateSnapshot(ctx, "saved")
			return err
		},
	}
}

func TestSandboxCaptureAndFork_whenSourceIsTransitioning(t *testing.T) {
	for resource, call := range transitionMethodCases() {
		t.Run(resource, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given one definitive refusal followed by an accepted operation.
				posts, reads := 0, 0
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
					switch r.Method {
					case http.MethodPost:
						assert.Equal(t, "/sandboxes/sbx/"+resource, r.URL.Path)
						posts++
						if posts == 1 {
							w.Header().Set("Content-Type", "application/json")
							w.WriteHeader(http.StatusConflict)
							writeJSON(t, w, map[string]any{
								"code": CodeSandboxTransitionInProgress, "source": SourceAPI, "retry_after_seconds": 2,
							})
							return
						}
						writeJSON(t, w, map[string]string{"id": "artifact"})
					case http.MethodGet:
						reads++
						switch resource {
						case "images":
							writeJSON(t, w, imageFixture("artifact", "pending"))
						case "snapshots":
							writeJSON(t, w, snapshotFixture("artifact", "creating"))
						case "fork":
							t.Error("NoWait must not read the source or fork")
						}
					}
				})
				started := time.Now()
				// When invoking the public operation.
				err := call(t.Context(), sandbox)
				// Then its POST is retried before the ordinary acknowledgement handling.
				require.NoError(t, err)
				require.Equal(t, 2, posts)
				require.Equal(t, 2*time.Second, time.Since(started))
				wantReads := 1
				if resource == "fork" {
					wantReads = 0
				}
				require.Equal(t, wantReads, reads)
			})
		})
	}
}

func TestSandboxCaptureAndFork_whenConflictIsNotTransition(t *testing.T) {
	for resource, call := range transitionMethodCases() {
		t.Run(resource, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given a definitive non-retryable conflict, such as attached volumes.
				calls := 0
				sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
					calls++
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusConflict)
					writeJSON(t, w, map[string]string{"code": CodeConflict, "source": SourceAPI})
				})
				started := time.Now()
				// When invoking the public operation.
				err := call(t.Context(), sandbox)
				// Then the failure is immediate and no duplicate operation is sent.
				require.ErrorIs(t, err, ErrConflict)
				require.Equal(t, 1, calls)
				require.Zero(t, time.Since(started))
			})
		})
	}
}

func TestFork_whenSubmissionRetriesKeepReadinessBudget(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a transition delay longer than the fork readiness budget.
		posts, forkReads := 0, 0
		source := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
			switch r.URL.Path {
			case "/sandboxes/sbx":
				writeJSON(t, w, sandboxFixture("sbx"))
			case "/sandboxes/sbx/fork":
				posts++
				if posts == 1 {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusConflict)
					writeJSON(t, w, map[string]any{"code": CodeSandboxTransitionInProgress, "retry_after_seconds": 5})
					return
				}
				writeJSON(t, w, map[string]string{"id": "fork", "status": "pending"})
			case "/sandboxes/fork":
				forkReads++
				detail := sandboxFixture("fork")
				detail["status"] = "pending"
				if r.URL.Query().Get("wait") == "running" {
					<-time.After(500 * time.Millisecond)
					detail["status"] = "running"
				}
				writeJSON(t, w, detail)
			default:
				t.Errorf("unexpected request: %s", r.URL.Path)
			}
		})
		started := time.Now()
		// When the accepted fork still needs a readiness hold.
		fork, err := source.Fork(t.Context(), &ForkOptions{Wait: time.Second})
		// Then the full readiness budget starts after acceptance.
		require.NoError(t, err)
		require.Equal(t, SandboxStateStarted, fork.State())
		require.Equal(t, 2, forkReads)
		require.Equal(t, 5500*time.Millisecond, time.Since(started))
	})
}
