// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

type dockerTimingTransport func(*http.Request) (*http.Response, error)

func (f dockerTimingTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func dockerTimingSandbox(t *testing.T, transport dockerTimingTransport) *Sandbox {
	t.Helper()
	// No network: this server value supplies only the helper's API base URL.
	client := newTestClient(t, &httptest.Server{URL: "http://api.invalid"},
		WithHTTPClient(&http.Client{Transport: transport}))
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetOptions{ToolboxURL: "http://toolbox.invalid", AccessToken: "caller"},
	})
	require.NoError(t, err)
	return sandbox
}

func dockerTimingResponse(state string) *http.Response {
	return &http.Response{
		StatusCode: http.StatusOK, Header: http.Header{"Content-Type": {"application/json"}},
		Body: io.NopCloser(strings.NewReader(`{"owner":"daemon","state":"` + state + `"}`)),
	}
}

func TestDockerWait_whenDefaultBudgetAndImmediatePolls(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		started := time.Now()
		var observations []time.Duration
		var holds []string
		sandbox := dockerTimingSandbox(t, func(request *http.Request) (*http.Response, error) {
			observations = append(observations, time.Since(started))
			holds = append(holds, request.URL.Query().Get("waitMs"))
			return dockerTimingResponse("deferred"), nil
		})
		// When
		_, err := sandbox.Docker().WaitUntilReady(t.Context(), nil)
		// Then
		var wait *DockerWaitTimeoutError
		require.ErrorAs(t, err, &wait)
		require.Equal(t, time.Minute, time.Since(started))
		require.Len(t, observations, 120)
		for i, observed := range observations {
			require.Equal(t, time.Duration(i)*500*time.Millisecond, observed)
		}
		require.Equal(t, "1000", holds[0])
		require.Equal(t, "500", holds[len(holds)-1])
	})
}

func TestDockerWait_whenUnboundedWaitExceedsDefault(t *testing.T) {
	for _, budget := range []time.Duration{WaitForever, maxTimerDuration + time.Nanosecond} {
		t.Run(budget.String(), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given
				started := time.Now()
				sandbox := dockerTimingSandbox(t, func(request *http.Request) (*http.Response, error) {
					require.Equal(t, "1000", request.URL.Query().Get("waitMs"))
					if time.Since(started) > time.Minute {
						return dockerTimingResponse("ready"), nil
					}
					return dockerTimingResponse("future"), nil
				})
				// When
				result, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: budget})
				// Then
				require.NoError(t, err)
				require.Equal(t, DockerStateReady, result.State)
				require.Equal(t, time.Minute+500*time.Millisecond, time.Since(started))
			})
		})
	}
}

func TestDockerWait_whenCanceledDuringPollSpacing(t *testing.T) {
	for _, closeClient := range []bool{false, true} {
		name := "caller"
		if closeClient {
			name = "client"
		}
		t.Run(name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given
				started := time.Now()
				ctx, cancel := context.WithCancel(t.Context())
				defer cancel()
				polls := 0
				sandbox := dockerTimingSandbox(t, func(_ *http.Request) (*http.Response, error) {
					polls++
					return dockerTimingResponse("starting"), nil
				})
				timer := time.AfterFunc(250*time.Millisecond, func() {
					if closeClient {
						require.NoError(t, sandbox.client.Close())
						return
					}
					cancel()
				})
				defer timer.Stop()
				// When
				_, err := sandbox.Docker().WaitUntilReady(ctx, &DockerWaitOptions{Timeout: WaitForever})
				// Then
				match := context.Canceled
				if closeClient {
					match = ErrClientClosed
				}
				require.ErrorIs(t, err, match)
				require.Equal(t, 250*time.Millisecond, time.Since(started))
				require.Equal(t, 1, polls)
			})
		})
	}
}
