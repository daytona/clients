// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func buildLogsErrorReply(t *testing.T, w http.ResponseWriter, status int, code, message string) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	writeJSON(t, w, map[string]string{
		"code": code, "message": message, "source": "DAYTONA_API", "trace_id": "trace-" + strings.ToLower(code),
	})
}

// buildLogsErrorFake answers the first discoveryErrors discoveries and the
// first streamErrors streams with the given error, then succeeds.
func buildLogsErrorFake(t *testing.T, discoveryErrors, streamErrors int32, status int, code string) *fakeServer {
	t.Helper()
	var discoveries, streams atomic.Int32
	return newFakeServer(t, map[string]http.HandlerFunc{
		"GET /images/image/build-logs-url": func(w http.ResponseWriter, r *http.Request) {
			if discoveries.Add(1) <= discoveryErrors {
				buildLogsErrorReply(t, w, status, code, "server says "+code)
				return
			}
			writeJSON(t, w, map[string]string{"url": "http://" + r.Host + "/build-logs/images/image"})
		},
		"GET /build-logs/images/image": func(w http.ResponseWriter, r *http.Request) {
			if streams.Add(1) <= streamErrors {
				buildLogsErrorReply(t, w, status, code, "server says "+code)
				return
			}
			buildLogsSSE(t, buildLogsDone("empty"))(w, r)
		},
	})
}

func followBuildLogsForTest(t *testing.T, fake *fakeServer) ([]time.Duration, error) {
	t.Helper()
	client := newTestClient(t, fake.Server)
	var delays []time.Duration
	follower := buildLogFollower{
		api: client.api(), path: "/images/image/build-logs-url", now: time.Now,
		jitter: func() float64 { return 1 },
		pause: func(_ context.Context, delay time.Duration) error {
			delays = append(delays, delay)
			return nil
		},
	}
	err := follower.follow(t.Context(), func(BuildLogEvent, error) bool { return true })
	return delays, err
}

func TestBuildLogsTransientHTTPErrorsAreRetried(t *testing.T) {
	for _, tt := range []struct {
		status int
		code   string
	}{{503, "SERVICE_UNAVAILABLE"}, {429, "TOO_MANY_REQUESTS"}, {429, "RATE_LIMITED"}} {
		t.Run(tt.code, func(t *testing.T) {
			// Given discovery answering one transient error before its URL.
			fake := buildLogsErrorFake(t, 1, 0, tt.status, tt.code)
			// When following.
			delays, err := followBuildLogsForTest(t, fake)
			// Then the error was one retried attempt, not the caller's answer.
			require.NoError(t, err)
			require.Len(t, delays, 1)
			require.Len(t, fake.requests(), 3)
		})
	}
}

func TestBuildLogsExhaustedRetriesKeepServerError(t *testing.T) {
	for _, phase := range []string{"discovery", "stream"} {
		t.Run(phase, func(t *testing.T) {
			// Given an endpoint that keeps answering 503 with a server error envelope.
			discoveryErrors, streamErrors := int32(6), int32(0)
			if phase == "stream" {
				discoveryErrors, streamErrors = 0, 6
			}
			fake := buildLogsErrorFake(t, discoveryErrors, streamErrors, 503, "SERVICE_UNAVAILABLE")
			// When following until the reconnect budget ends.
			delays, err := followBuildLogsForTest(t, fake)
			// Then the server's status, code, message and trace ID are the cause.
			var interrupted *BuildLogInterruptedError
			require.ErrorAs(t, err, &interrupted)
			require.Contains(t, interrupted.Error(), "reconnect budget exhausted")
			var budget, server *Error
			require.ErrorAs(t, interrupted.SDKError.Err, &budget)
			require.Equal(t, CodeBuildLogInterrupted, budget.Code)
			require.ErrorAs(t, budget.Err, &server)
			require.Equal(t, 503, server.StatusCode)
			require.Equal(t, "SERVICE_UNAVAILABLE", server.Code)
			require.Equal(t, "server says SERVICE_UNAVAILABLE", server.Message)
			require.Equal(t, "trace-service_unavailable", server.TraceID)
			require.Len(t, delays, 5)
		})
	}
}

func TestBuildLogsPermanentHTTPErrorsAreNotRetried(t *testing.T) {
	for _, tt := range []struct {
		status int
		code   string
	}{
		{429, "LIMIT_EXCEEDED"},
		{503, "V0_BACKUP_REGISTRY_UNCONFIGURED"},
		{501, "WORKLOAD_PLANE_ELSEWHERE"},
		{501, "NOT_IMPLEMENTED"},
		{501, "FUTURE_CODE"},
	} {
		t.Run("discovery "+tt.code, func(t *testing.T) {
			// Given a 429 / 5xx whose code names a state a retry cannot change.
			fake := buildLogsErrorFake(t, 1, 0, tt.status, tt.code)
			// When following.
			delays, err := followBuildLogsForTest(t, fake)
			// Then the typed error is the answer at once, as a 404 is.
			var interrupted *BuildLogInterruptedError
			require.NotErrorAs(t, err, &interrupted)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Equal(t, tt.status, sdkError.StatusCode)
			require.Equal(t, tt.code, sdkError.Code)
			require.Empty(t, delays)
			require.Len(t, fake.requests(), 1)
		})
		t.Run("stream "+tt.code, func(t *testing.T) {
			fake := buildLogsErrorFake(t, 0, 1, tt.status, tt.code)
			delays, err := followBuildLogsForTest(t, fake)
			var interrupted *BuildLogInterruptedError
			require.ErrorAs(t, err, &interrupted)
			require.Equal(t, tt.code, interrupted.SDKError.Code)
			require.Equal(t, tt.status, interrupted.SDKError.StatusCode)
			require.Empty(t, delays)
			require.Len(t, fake.requests(), 2)
		})
	}
}

func TestBuildLogsPermanentCodesAreRetryableStatusCatalogCodes(t *testing.T) {
	for code := range buildLogPermanentCodes {
		status := catalogStatus("DAYTONA_API", code)
		// A 501 is permanent anyway; the list names retryable statuses only.
		require.True(t, status == 429 || status >= 500 && status != 501, "%s: %d", code, status)
	}
}
