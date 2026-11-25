// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"sync/atomic"
	"syscall"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

// Docker and artifact readiness retry a transient read failure under the
// wait's own deadline, as sandbox waits do (sandbox_wait_retry_test.go). The
// Python twin is tests/test_wait_read_retries.py; TypeScript's is
// tests/wait-read-retries.test.ts.

func TestReadRetryable_classifiesFailures(t *testing.T) {
	for _, test := range []struct {
		name string
		err  error
		want bool
	}{
		{"500", &Error{StatusCode: 500}, true},
		{"502", &Error{StatusCode: 502}, true},
		{"503", &Error{StatusCode: 503, Code: "SERVICE_UNAVAILABLE"}, true},
		{"504", &Error{StatusCode: 504}, true},
		{"429", &Error{StatusCode: 429, Code: "RATE_LIMITED"}, true},
		{"connection", &Error{Source: SourceSDK, category: ErrConnection}, true},
		{"client timeout", &Error{StatusCode: 408, Code: CodeRequestTimeout, Source: SourceSDK, category: ErrClientTimeout}, true},
		{"400", &Error{StatusCode: 400}, false},
		{"401", &Error{StatusCode: 401}, false},
		{"403", &Error{StatusCode: 403}, false},
		{"404", &Error{StatusCode: 404}, false},
		{"server 408", &Error{StatusCode: 408, Code: CodeRequestTimeout, Source: SourceAPI}, false},
		{"501", &Error{StatusCode: 501}, false},
		{"limit exceeded", &Error{StatusCode: 429, Code: CodeLimitExceeded}, false},
		{"wait timeout", waitTimeout(&Error{StatusCode: 503}), false},
		{"not an SDK error", errors.New("plain"), false},
	} {
		t.Run(test.name, func(t *testing.T) {
			require.Equal(t, test.want, readRetryable(test.err))
		})
	}
}

func TestReadRetryDelay_doublesToFiveSecondsAndHonorsRetryAfter(t *testing.T) {
	delays := make([]time.Duration, 0, 7)
	var backoff time.Duration
	for range 7 {
		var pause time.Duration
		backoff, pause = readRetryDelay(backoff, &Error{StatusCode: 503})
		delays = append(delays, pause)
	}
	require.Equal(t, []time.Duration{
		200 * time.Millisecond, 400 * time.Millisecond, 800 * time.Millisecond, 1600 * time.Millisecond,
		3200 * time.Millisecond, 5 * time.Second, 5 * time.Second,
	}, delays)
	_, pause := readRetryDelay(0, &Error{StatusCode: 429, RetryAfter: 2 * time.Second})
	require.Equal(t, 2*time.Second, pause)
}

func retryResponse(t *testing.T, w http.ResponseWriter, status int) {
	if status == 0 {
		panic(http.ErrAbortHandler)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	writeJSON(t, w, map[string]string{"code": "X", "message": "transient"})
}

type retryTransport struct{ handler http.Handler }

func (transport retryTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	var aborted bool
	response, err := func() (response *http.Response, err error) {
		defer func() {
			if recovered := recover(); recovered != nil {
				if recovered != http.ErrAbortHandler { //nolint:errorlint // ErrAbortHandler is panicked unwrapped.
					panic(recovered)
				}
				aborted = true
			}
		}()
		return imageHandlerTransport(transport).RoundTrip(request)
	}()
	if aborted {
		return nil, syscall.ECONNRESET
	}
	return response, err
}

// dockerFake is created outside the synctest bubble (its listener is real);
// virtualDocker serves it in-process inside the bubble.
func dockerFake(t *testing.T, handler *http.HandlerFunc) *fakeServer {
	t.Helper()
	return newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": func(w http.ResponseWriter, r *http.Request) {
		(*handler)(w, r)
	}})
}

func virtualDocker(t *testing.T, fake *fakeServer) *Sandbox {
	t.Helper()
	client := newTestClient(t, fake.Server, WithHTTPClient(&http.Client{Transport: retryTransport{handler: fake.Server.Config.Handler}}))
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetSandboxOptions{ToolboxURL: fake.Server.URL, AccessToken: "caller"},
	})
	require.NoError(t, err)
	return sandbox
}

func TestDockerWait_whenStatusReadFailsOnce_retries(t *testing.T) {
	for _, status := range []int{500, 502, 503, 504, 429, 0} {
		t.Run(strconv.Itoa(status), func(t *testing.T) {
			var handler http.HandlerFunc
			fake := dockerFake(t, &handler)
			synctest.Test(t, func(t *testing.T) {
				// Given: a transient failure between two status reads.
				var reads []time.Time
				handler = func(w http.ResponseWriter, _ *http.Request) {
					reads = append(reads, time.Now())
					switch len(reads) {
					case 1:
						writeJSON(t, w, map[string]string{"owner": "daemon", "state": "starting"})
					case 2:
						retryResponse(t, w, status)
					default:
						writeJSON(t, w, map[string]string{"owner": "daemon", "state": "ready"})
					}
				}
				sandbox := virtualDocker(t, fake)

				// When
				result, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: 20 * time.Second})

				// Then
				require.NoError(t, err)
				require.Equal(t, DockerStateReady, result.State)
				require.Len(t, reads, 3)
				// The 500 ms poll spacing outlasts the first 200 ms backoff.
				require.Equal(t, 500*time.Millisecond, reads[2].Sub(reads[1]))
			})
		})
	}
}

func TestDockerWait_whenStatusReadKeepsFailing_timesOutWithCause(t *testing.T) {
	var handler http.HandlerFunc
	fake := dockerFake(t, &handler)
	synctest.Test(t, func(t *testing.T) {
		// Given
		var reads atomic.Int32
		handler = func(w http.ResponseWriter, _ *http.Request) {
			if reads.Add(1) == 1 {
				writeJSON(t, w, map[string]string{"owner": "daemon", "state": "starting"})
				return
			}
			retryResponse(t, w, http.StatusServiceUnavailable)
		}
		sandbox := virtualDocker(t, fake)

		// When
		_, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: 2 * time.Second})

		// Then: the Docker timeout keeps the last status and wraps the last failure.
		var timeout *DockerWaitTimeoutError
		require.ErrorAs(t, err, &timeout)
		require.NotNil(t, timeout.Status)
		require.Equal(t, DockerStateStarting, timeout.Status.State)
		var cause *Error
		require.ErrorAs(t, errors.Unwrap(timeout.SDKError), &cause)
		require.Equal(t, http.StatusServiceUnavailable, cause.StatusCode)
	})
}

func TestDockerWait_whenStatusReadFailsPermanently_returnsAtOnce(t *testing.T) {
	for _, status := range []int{400, 401, 403, 404, 501} {
		t.Run(strconv.Itoa(status), func(t *testing.T) {
			var handler http.HandlerFunc
			fake := dockerFake(t, &handler)
			synctest.Test(t, func(t *testing.T) {
				// Given
				var reads atomic.Int32
				handler = func(w http.ResponseWriter, _ *http.Request) {
					reads.Add(1)
					retryResponse(t, w, status)
				}
				sandbox := virtualDocker(t, fake)

				// When
				_, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: 20 * time.Second})

				// Then
				var detail *Error
				require.ErrorAs(t, err, &detail)
				require.Equal(t, status, detail.StatusCode)
				require.EqualValues(t, 1, reads.Load())
			})
		})
	}
}

func TestArtifactGet_whenHoldFailsOnce_retries(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		for _, status := range []int{500, 502, 503, 504, 429, 0} {
			t.Run(resource.name+"/"+strconv.Itoa(status), func(t *testing.T) {
				// Given: a transient failure, then a settled artifact.
				var reads atomic.Int32
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /" + resource.name + "/{id}": func(w http.ResponseWriter, _ *http.Request) {
						if reads.Add(1) == 1 {
							retryResponse(t, w, status)
							return
						}
						writeJSON(t, w, resource.fixture("artifact/one", "failed"))
					},
				})
				synctest.Test(t, func(t *testing.T) {
					client := newTestClient(t, fake.Server, WithHTTPClient(&http.Client{Transport: retryTransport{handler: fake.Server.Config.Handler}}))

					// When
					got, err := resource.get(t.Context(), client, &GetArtifactOptions{Wait: true, Timeout: 10 * time.Minute})

					// Then: the settled status is returned for the caller to check.
					require.NoError(t, err)
					require.Equal(t, "failed", got)
					require.EqualValues(t, 2, reads.Load())
				})
			})
		}
	}
}

func TestArtifactGet_whenHoldStallsBeforeTheBudget_retries(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		t.Run(resource.name, func(t *testing.T) {
			// Given: a first hold that outlives its read slack, with most of the budget left.
			var reads atomic.Int32
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /" + resource.name + "/{id}": func(w http.ResponseWriter, r *http.Request) {
					if reads.Add(1) == 1 {
						<-r.Context().Done()
						return
					}
					writeJSON(t, w, resource.fixture("artifact/one", "ready"))
				},
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, time.Minute)

				// When
				got, err := resource.get(t.Context(), client, &GetArtifactOptions{Wait: true, Timeout: 10 * time.Minute})

				// Then
				require.NoError(t, err)
				require.Equal(t, "ready", got)
				require.EqualValues(t, 2, reads.Load())
			})
		})
	}
}

func TestArtifactGet_whenHoldKeepsFailing_timesOutWithCause(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		t.Run(resource.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /" + resource.name + "/{id}": func(w http.ResponseWriter, _ *http.Request) {
					retryResponse(t, w, http.StatusServiceUnavailable)
				},
			})
			synctest.Test(t, func(t *testing.T) {
				client := newTestClient(t, fake.Server, WithHTTPClient(&http.Client{Transport: retryTransport{handler: fake.Server.Config.Handler}}))
				began := time.Now()

				// When
				_, err := resource.get(t.Context(), client, &GetArtifactOptions{Wait: true, Timeout: 2 * time.Second})

				// Then
				require.ErrorIs(t, err, ErrRequestTimeout)
				require.Equal(t, 2*time.Second, time.Since(began))
				var sdkError *Error
				require.ErrorAs(t, err, &sdkError)
				require.Equal(t, "artifact did not become ready before timeout", sdkError.Message)
				require.ErrorIs(t, err, ErrServiceUnavailable)
			})
		})
	}
}

func TestArtifactGet_whenHoldFailsPermanently_returnsAtOnce(t *testing.T) {
	for _, resource := range imageArtifactResources() {
		t.Run(resource.name, func(t *testing.T) {
			var reads atomic.Int32
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /" + resource.name + "/{id}": func(w http.ResponseWriter, _ *http.Request) {
					reads.Add(1)
					retryResponse(t, w, http.StatusNotFound)
				},
			})
			synctest.Test(t, func(t *testing.T) {
				client := newTestClient(t, fake.Server, WithHTTPClient(&http.Client{Transport: retryTransport{handler: fake.Server.Config.Handler}}))
				_, err := resource.get(t.Context(), client, &GetArtifactOptions{Wait: true, Timeout: time.Minute})
				require.ErrorIs(t, err, ErrNotFound)
				require.EqualValues(t, 1, reads.Load())
			})
		})
	}
}

func TestImagesReady_whenHoldFailsOnce_retries(t *testing.T) {
	// Given: a pending image whose next readiness hold fails once.
	var reads atomic.Int32
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /images/{id}": func(w http.ResponseWriter, _ *http.Request) {
			if reads.Add(1) == 1 {
				retryResponse(t, w, http.StatusBadGateway)
				return
			}
			writeJSON(t, w, imageFixture("img", "ready"))
		},
	})
	synctest.Test(t, func(t *testing.T) {
		client := newTestClient(t, fake.Server, WithHTTPClient(&http.Client{Transport: retryTransport{handler: fake.Server.Config.Handler}}))
		raw, err := json.Marshal(imageFixture("img", "pending"))
		require.NoError(t, err)
		pending, err := parseImage(raw)
		require.NoError(t, err)
		ctx, cancel := context.WithTimeout(t.Context(), time.Minute)
		defer cancel()

		// When
		image, err := client.Images().ready(ctx, pending, "failed")

		// Then
		require.NoError(t, err)
		require.Equal(t, ImageStatusReady, image.Status)
		require.EqualValues(t, 2, reads.Load())
	})
}

func TestDockerWait_whenStatusReadFails_keepsThePollSpacing(t *testing.T) {
	var handler http.HandlerFunc
	fake := dockerFake(t, &handler)
	synctest.Test(t, func(t *testing.T) {
		// Given: a rate-limited read without a hint, then readiness.
		var reads []time.Time
		handler = func(w http.ResponseWriter, _ *http.Request) {
			reads = append(reads, time.Now())
			if len(reads) == 1 {
				retryResponse(t, w, http.StatusTooManyRequests)
				return
			}
			writeJSON(t, w, map[string]string{"owner": "daemon", "state": "ready"})
		}
		sandbox := virtualDocker(t, fake)

		// When
		_, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: 20 * time.Second})

		// Then: the 500 ms poll spacing outlasts the 200 ms backoff.
		require.NoError(t, err)
		require.Len(t, reads, 2)
		require.Equal(t, 500*time.Millisecond, reads[1].Sub(reads[0]))
	})
}

func TestImagesPull_whenFirstLookupFailsOnce_retriesWhenWaiting(t *testing.T) {
	for _, wait := range []bool{true, false} {
		t.Run(strconv.FormatBool(wait), func(t *testing.T) {
			// Given: the lookup after the submission fails once.
			var lookups atomic.Int32
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /images": jsonHandler(t, map[string]string{"id": "img"}),
				"GET /images/img": func(w http.ResponseWriter, _ *http.Request) {
					if lookups.Add(1) == 1 {
						retryResponse(t, w, http.StatusBadGateway)
						return
					}
					writeJSON(t, w, imageFixture("img", "ready"))
				},
			})
			synctest.Test(t, func(t *testing.T) {
				client := newTestClient(t, fake.Server, WithHTTPClient(&http.Client{Transport: retryTransport{handler: fake.Server.Config.Handler}}))

				// When
				image, err := client.Images().Pull(t.Context(), "alpine", &PullImageOptions{Wait: &wait, Timeout: time.Minute})

				// Then: only a waiting pull reads the lookup again; the POST is never repeated.
				if !wait {
					require.ErrorIs(t, err, ErrInternalServer)
					require.EqualValues(t, 1, lookups.Load())
					return
				}
				require.NoError(t, err)
				require.Equal(t, ImageStatusReady, image.Status)
				require.EqualValues(t, 2, lookups.Load())
				posts := 0
				for _, request := range fake.requests() {
					if request.Method == http.MethodPost {
						posts++
					}
				}
				require.Equal(t, 1, posts)
			})
		})
	}
}

func TestReadBackoff_whenContextEnded_keepsTheTransientFailure(t *testing.T) {
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	var retry readBackoff
	failure := &Error{StatusCode: http.StatusServiceUnavailable}
	_, retryable := retry.next(ctx, failure)
	require.False(t, retryable)
	require.Same(t, failure, retry.lastErr)
}

func TestParseRetryAfter_trimsSpace(t *testing.T) {
	require.Equal(t, 5*time.Second, parseRetryAfter(" 5 "))
}

func TestArtifactGet_whenHoldIsRateLimited_keepsThePollSpacing(t *testing.T) {
	var reads []time.Time
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /images/{id}": func(w http.ResponseWriter, _ *http.Request) {
			reads = append(reads, time.Now())
			if len(reads) == 1 {
				retryResponse(t, w, http.StatusTooManyRequests)
				return
			}
			writeJSON(t, w, imageFixture("img", "ready"))
		},
	})
	synctest.Test(t, func(t *testing.T) {
		client := newTestClient(t, fake.Server, WithHTTPClient(&http.Client{Transport: retryTransport{handler: fake.Server.Config.Handler}}))
		_, err := client.Images().Get(t.Context(), "img", &GetArtifactOptions{Wait: true, Timeout: time.Minute})
		require.NoError(t, err)
		require.Len(t, reads, 2)
		require.Equal(t, time.Second, reads[1].Sub(reads[0]))
	})
}
