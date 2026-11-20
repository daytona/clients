// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strconv"
	"syscall"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

type waitRetryTransport func(*http.Request) (*http.Response, error)

func (f waitRetryTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func waitRetryResponse(status int) (*http.Response, error) {
	if status == 0 {
		return nil, syscall.ECONNRESET
	}
	response := httptest.NewRecorder()
	response.WriteHeader(status)
	return response.Result(), nil
}

func TestWaitRetry_whenReadFailsOnce_reachesTarget(t *testing.T) {
	operations := []struct {
		name   string
		status string
		call   func(context.Context, *Sandbox) (*Sandbox, error)
	}{
		{"Stop", "stopped", func(ctx context.Context, s *Sandbox) (*Sandbox, error) {
			return s, s.Stop(ctx, &LifecycleWaitOptions{Timeout: 20 * time.Second})
		}},
		{"Wait", "running", func(ctx context.Context, s *Sandbox) (*Sandbox, error) {
			return s, s.Wait(ctx, &WaitOptions{Timeout: 20 * time.Second})
		}},
		{"Create", "running", func(ctx context.Context, s *Sandbox) (*Sandbox, error) {
			return s.client.Create(ctx, "", nil)
		}},
	}
	for _, operation := range operations {
		for _, mode := range []string{"live", "closed", "unavailable"} {
			for _, status := range []int{502, 503, 504, 0, 429} {
				t.Run(operation.name+"/"+mode+"/"+strconv.Itoa(status), func(t *testing.T) {
					synctest.Test(t, func(t *testing.T) {
						// Given: accepted operations and a single failed read before the target.
						t.Setenv("DAYTONA_SANDBOX_EVENTS", "1")
						sandbox := virtualWaitSandbox(t, func(w http.ResponseWriter, r *http.Request) {
							switch r.Method + " " + r.URL.Path {
							case "POST /sandboxes":
								writeJSON(t, w, map[string]string{"id": "sbx", "status": "pending"})
							case "POST /sandboxes/sbx/stop":
								writeJSON(t, w, struct{}{})
							case "GET /sandboxes/sbx":
								detail := sandboxFixture("sbx")
								detail["status"] = operation.status
								writeJSON(t, w, detail)
							default:
								t.Errorf("unexpected request: %s %s", r.Method, r.URL)
							}
						})
						source := &waitTestSource{events: make(chan stateObservation)}
						switch mode {
						case "closed":
							close(source.events)
						case "unavailable":
							source.events = nil
						}
						sandbox.client.stateSource = source
						base := sandbox.client.http.Transport
						var reads []time.Time
						sandbox.client.http.Transport = waitRetryTransport(func(r *http.Request) (*http.Response, error) {
							if r.Method == http.MethodGet {
								reads = append(reads, time.Now())
								if len(reads) == 1 {
									return waitRetryResponse(status)
								}
							}
							return base.RoundTrip(r)
						})

						// When
						result, err := operation.call(t.Context(), sandbox)

						// Then: Create must return the started handle, not silently return pending.
						require.NoError(t, err)
						require.Equal(t, sandboxState(operation.status), result.State())
						require.Len(t, reads, 2)
						require.GreaterOrEqual(t, reads[1].Sub(reads[0]), waitMinInterval)
						require.EqualValues(t, 1, source.released.Load())
					})
				})
			}
		}
	}
}

func TestWaitRetry_whenEventsDisabled_propagatesReadError(t *testing.T) {
	for _, mode := range []string{"config", "0", "false", "off", " FALSE "} {
		for _, status := range []int{502, 503, 504, 0, 429} {
			t.Run(mode+"/"+strconv.Itoa(status), func(t *testing.T) {
				synctest.Test(t, func(t *testing.T) {
					// Given: direct polling, including an explicitly disabled installed hub.
					sandbox := virtualWaitSandbox(t, func(http.ResponseWriter, *http.Request) {
						t.Error("failed read must not be retried")
					})
					if mode != "config" {
						t.Setenv("DAYTONA_SANDBOX_EVENTS", mode)
						sandbox.client.stateSource = &eventsStateSource{}
						// This source has no hub; the ambient switch bypasses it.
						t.Cleanup(func() { sandbox.client.stateSource = noopStateSource{} })
					}
					reads := 0
					sandbox.client.http.Transport = waitRetryTransport(func(*http.Request) (*http.Response, error) {
						reads++
						return waitRetryResponse(status)
					})
					began := time.Now()

					// When
					err := sandbox.Wait(t.Context(), nil)

					// Then
					require.Error(t, err)
					if status == 0 {
						require.ErrorIs(t, err, syscall.ECONNRESET)
					} else {
						var detail *Error
						require.ErrorAs(t, err, &detail)
						require.Equal(t, status, detail.StatusCode)
					}
					require.Equal(t, 1, reads)
					require.Zero(t, time.Since(began))
				})
			})
		}
	}
}
