// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCreateReturnsAcceptedHandle_whenCallerEndsWait(t *testing.T) {
	routes := append(createTestCases(), createTestCase{
		name: "fork", path: "/sandboxes/source/fork", class: SandboxClassLinux,
		call: func(ctx context.Context, client *Client, opts *CreateFromSnapshotOptions) (*Sandbox, error) {
			source, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "source", Class: SandboxClassLinux}})
			if err != nil {
				return nil, err
			}
			return source.Fork(ctx, &ForkOptions{Wait: opts.Wait})
		},
	})
	for _, route := range routes {
		for _, deadline := range []bool{false, true} {
			for _, phase := range []string{"detail", "long poll"} {
				if route.name != "fork" && phase == "detail" {
					continue // Only fork has an initial detail read before its wait.
				}
				t.Run(route.name+"/"+contextEndName(deadline)+"/"+phase, func(t *testing.T) {
					// Given: an accepted sandbox and a blocked observation.
					arrived := make(chan struct{}, 1)
					fake := newFakeServer(t, map[string]http.HandlerFunc{
						"GET /sandboxes/source": jsonHandler(t, sandboxFixture("source")),
						"POST " + route.path:    jsonHandler(t, map[string]string{"id": "sbx", "status": "pending"}),
						"GET /sandboxes/sbx": func(w http.ResponseWriter, r *http.Request) {
							if phase == "long poll" && r.URL.Query().Get("wait") == "" {
								detail := sandboxFixture("sbx")
								detail["status"] = "pending"
								writeJSON(t, w, detail)
								return
							}
							arrived <- struct{}{}
							<-r.Context().Done()
						},
						"DELETE /sandboxes/sbx": jsonHandler(t, struct{}{}),
					})
					synctest.Test(t, func(t *testing.T) {
						client := imageClockClient(t, fake, NoTimeout)
						ctx, cancel := contextEnd(t, deadline)
						defer cancel()
						go func() {
							<-arrived
							if !deadline {
								cancel()
							}
						}()
						// When
						sandbox, err := route.call(ctx, client, &CreateFromSnapshotOptions{})
						// Then: the caller owns the accepted resource even on error.
						require.ErrorIs(t, err, contextEndError(deadline))
						require.NotNil(t, sandbox)
						require.Equal(t, "sbx", sandbox.ID())
						require.Equal(t, SandboxStateCreating, sandbox.State())
						require.NoError(t, sandbox.Delete(t.Context(), &LifecycleWaitOptions{Timeout: NoWait}))
					})
				})
			}
		}
	}
}

func TestCreatePreservesKey_whenCallerEndsPOST(t *testing.T) {
	for _, route := range createTestCases() {
		for _, deadline := range []bool{false, true} {
			t.Run(route.name+"/"+contextEndName(deadline), func(t *testing.T) {
				// Given: a POST received by the server without a response.
				arrived := make(chan struct{}, 1)
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST " + route.path: func(_ http.ResponseWriter, r *http.Request) {
						arrived <- struct{}{}
						<-r.Context().Done()
					},
				})
				synctest.Test(t, func(t *testing.T) {
					client := imageClockClient(t, fake, NoTimeout)
					ctx, cancel := contextEnd(t, deadline)
					defer cancel()
					go func() {
						<-arrived
						if !deadline {
							cancel()
						}
					}()
					// When
					sandbox, err := route.call(ctx, client, &CreateFromSnapshotOptions{Wait: NoWait})
					// Then: the actual generated key and context cause survive.
					require.Nil(t, sandbox)
					require.ErrorIs(t, err, contextEndError(deadline))
					key := fake.requests()[0].Header.Get("Idempotency-Key")
					require.NotEmpty(t, key)
					if deadline {
						var uncertain *SandboxCreateRequestTimeoutError
						require.ErrorAs(t, err, &uncertain)
						require.Equal(t, key, uncertain.IdempotencyKey)
						require.ErrorIs(t, uncertain.SDKError.Err, context.DeadlineExceeded)
					} else {
						var uncertain *SandboxCreateConnectionError
						require.ErrorAs(t, err, &uncertain)
						require.Equal(t, key, uncertain.IdempotencyKey)
						require.ErrorIs(t, uncertain.SDKError.Err, context.Canceled)
						require.ErrorIs(t, err, ErrConnection)
					}
				})
			})
		}
	}
}

func TestForkPreservesContext_whenCallerEndsPOST(t *testing.T) {
	for _, deadline := range []bool{false, true} {
		t.Run(contextEndName(deadline), func(t *testing.T) {
			// Given: fork has no idempotency contract, so its unknown outcome
			// must not promise safe replay with a fabricated recovery key.
			arrived := make(chan struct{}, 1)
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/source/fork": func(_ http.ResponseWriter, r *http.Request) {
					arrived <- struct{}{}
					<-r.Context().Done()
				},
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, NoTimeout)
				source, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "source"}})
				require.NoError(t, err)
				ctx, cancel := contextEnd(t, deadline)
				defer cancel()
				go func() {
					<-arrived
					if !deadline {
						cancel()
					}
				}()
				// When
				fork, err := source.Fork(ctx, &ForkOptions{Wait: NoWait})
				// Then
				require.Nil(t, fork)
				require.ErrorIs(t, err, contextEndError(deadline))
				require.Empty(t, fake.requests()[0].Header.Get("Idempotency-Key"))
			})
		})
	}
}

func contextEnd(t *testing.T, deadline bool) (context.Context, context.CancelFunc) {
	t.Helper()
	if deadline {
		return context.WithTimeout(t.Context(), time.Second)
	}
	return context.WithCancel(t.Context())
}

func contextEndName(deadline bool) string {
	if deadline {
		return "deadline"
	}
	return "cancel"
}

func contextEndError(deadline bool) error {
	if deadline {
		return context.DeadlineExceeded
	}
	return context.Canceled
}
