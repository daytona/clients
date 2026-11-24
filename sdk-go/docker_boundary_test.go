// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestDockerAndVolumes_whenResponseIsMalformed(t *testing.T) {
	for _, test := range []struct {
		name     string
		payloads []string
	}{
		{"status", []string{
			`null`, `[]`, `{}`, `{"owner":null,"state":"ready"}`, `{"owner":3,"state":"ready"}`,
			`{"owner":"daemon","state":false}`, `{"owner":"daemon","state":"ready","reason":3}`, `{"owner":"daemon","state":"ready","processId":{}}`,
		}},
		{"wait", []string{`{}`, `{"owner":"daemon","state":null}`, `{"owner":"daemon","state":"ready","reason":[]}`}},
		{"pull", []string{
			`null`, `[]`, `{}`, `{"requestId":null,"status":"pending","manifestId":null}`,
			`{"requestId":"r","status":5,"manifestId":null}`, `{"requestId":"r","status":"pulled"}`,
			`{"requestId":"r","status":"pulled","manifestId":3}`, `{"requestId":3,"status":"pulled","manifestId":null}`,
		}},
	} {
		for _, payload := range test.payloads {
			t.Run(test.name+"/"+payload, func(t *testing.T) {
				// Given
				fake := newFakeServer(t, map[string]http.HandlerFunc{"/": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					_, err := w.Write([]byte(payload))
					if err != nil {
						t.Error(err)
					}
				}})
				sandbox := dockerVolumeSandbox(t, fake)
				// When
				var err error
				switch test.name {
				case "status":
					_, err = sandbox.Docker().Status(t.Context())
				case "wait":
					_, err = sandbox.Docker().WaitUntilReady(t.Context(), nil)
				case "pull":
					_, err = sandbox.Volumes().Pull(t.Context(), "v", "r", nil)
				}
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.Len(t, fake.requests(), 1)
			})
		}
	}
}

func TestDockerAndVolumes_whenServerReturnsError(t *testing.T) {
	for _, operation := range []string{"status", "wait", "pull"} {
		for _, test := range []struct {
			status int
			code   string
			match  error
		}{
			{404, "NOT_FOUND", ErrNotFound},
			{429, "TOO_MANY_REQUESTS", ErrRateLimit},
			{503, "SERVICE_UNAVAILABLE", ErrServiceUnavailable},
			{409, "CONFLICT", ErrConflict},
			{400, "UNSUPPORTED_OPERATION", ErrUnsupportedOperation},
		} {
			if operation == "wait" && readRetryable(&Error{StatusCode: test.status, Code: test.code}) {
				continue // A readiness wait retries these (docker_wait_retry_test.go).
			}
			t.Run(operation+"/"+test.code, func(t *testing.T) {
				// Given
				fake := newFakeServer(t, map[string]http.HandlerFunc{"/": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(test.status)
					writeJSON(t, w, map[string]any{"code": test.code, "source": SourceDaemon, "message": "not ready", "requestId": "r", "retryAfterMs": 1000})
				}})
				sandbox := dockerVolumeSandbox(t, fake)
				// When
				var err error
				switch operation {
				case "status":
					_, err = sandbox.Docker().Status(t.Context())
				case "wait":
					_, err = sandbox.Docker().WaitUntilReady(t.Context(), nil)
				case "pull":
					_, err = sandbox.Volumes().Pull(t.Context(), "v", "r", nil)
				}
				// Then
				require.ErrorIs(t, err, test.match)
				var sdk *Error
				require.ErrorAs(t, err, &sdk)
				require.Equal(t, test.code, sdk.Code)
				require.Equal(t, "r", sdk.RequestID)
				require.Equal(t, time.Second, sdk.RetryAfter)
				require.Len(t, fake.requests(), 1)
			})
		}
	}
}

func TestDockerAndVolumes_whenCallerContextEnded(t *testing.T) {
	for _, operation := range []string{"status", "wait", "pull"} {
		for _, deadline := range []bool{false, true} {
			t.Run(operation+map[bool]string{true: "/deadline", false: "/cancel"}[deadline], func(t *testing.T) {
				// Given
				ctx, cancel := context.WithCancel(t.Context())
				expected := context.Canceled
				if deadline {
					cancel()
					ctx, cancel = context.WithDeadline(t.Context(), time.Unix(1, 0))
					expected = context.DeadlineExceeded
				} else {
					cancel()
				}
				defer cancel()
				fake := newFakeServer(t, nil)
				sandbox := dockerVolumeSandbox(t, fake)
				// When
				var err error
				switch operation {
				case "status":
					_, err = sandbox.Docker().Status(ctx)
				case "wait":
					_, err = sandbox.Docker().WaitUntilReady(ctx, nil)
				case "pull":
					_, err = sandbox.Volumes().Pull(ctx, "v", "r", nil)
				}
				// Then
				require.ErrorIs(t, err, expected)
				require.Empty(t, fake.requests())
			})
		}
	}
}

func TestDockerStatus_whenOptionalFieldsNull(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": jsonHandler(t, map[string]any{
		"owner": "daemon", "state": "ready", "reason": nil, "processId": nil, "future": true,
	})})
	sandbox := dockerVolumeSandbox(t, fake)
	// When
	status, err := sandbox.Docker().Status(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, DockerStateReady, status.State)
	require.Empty(t, status.ProcessID)
	require.Empty(t, status.Reason)
}

func TestDockerWait_whenActiveRequestCanceled(t *testing.T) {
	// Given
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": func(_ http.ResponseWriter, r *http.Request) {
		cancel()
		<-r.Context().Done()
	}})
	sandbox := dockerVolumeSandbox(t, fake)
	// When
	_, err := sandbox.Docker().WaitUntilReady(ctx, &DockerWaitOptions{Timeout: WaitForever})
	// Then
	require.ErrorIs(t, err, context.Canceled)
	var timeout *DockerWaitTimeoutError
	require.NotErrorAs(t, err, &timeout)
	require.Len(t, fake.requests(), 1)
}
