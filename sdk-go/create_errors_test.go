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

func TestCreateErrorsPreserveSubmissionFailures(t *testing.T) {
	for _, route := range createTestCases() {
		t.Run(route.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST " + route.path: func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusRequestTimeout)
					writeJSON(t, w, map[string]string{"code": CodeRequestTimeout, "source": "DAYTONA_API", "trace_id": "trace-create"})
				},
			})
			client := newTestClient(t, fake.Server)
			// When
			sandbox, err := route.call(t.Context(), client, &CreateFromSnapshotOptions{Wait: NoWait})
			// Then
			require.Nil(t, sandbox)
			require.ErrorIs(t, err, ErrRequestTimeout)
			require.NotErrorIs(t, err, ErrClientTimeout)
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.Equal(t, "DAYTONA_API", failure.Source)
			require.Equal(t, "trace-create", failure.TraceID)
			var uncertain *SandboxCreateRequestTimeoutError
			require.NotErrorAs(t, err, &uncertain)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestCreateErrorsPreserveKeyAfterLostResponse(t *testing.T) {
	for _, route := range createTestCases() {
		t.Run(route.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST " + route.path: func(w http.ResponseWriter, _ *http.Request) {
					loseCreateResponse(t, w)
				},
			})
			client := newTestClient(t, fake.Server)
			// When
			_, err := route.call(t.Context(), client, &CreateFromSnapshotOptions{Wait: NoWait, IdempotencyKey: "retry-create"})
			// Then
			var uncertain *SandboxCreateConnectionError
			require.ErrorAs(t, err, &uncertain)
			require.ErrorIs(t, err, ErrConnection)
			require.Equal(t, "retry-create", uncertain.IdempotencyKey)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func loseCreateResponse(t *testing.T, w http.ResponseWriter) {
	t.Helper()
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		t.Error("HTTP server does not support hijacking")
		return
	}
	conn, _, err := hijacker.Hijack()
	if err != nil {
		t.Errorf("hijack accepted create: %v", err)
		return
	}
	if closeErr := conn.Close(); closeErr != nil {
		t.Errorf("close accepted create: %v", closeErr)
	}
}

func TestCreateErrorsPreserveKeyAfterRequestTimeout(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes": func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() },
	})
	client := newTestClient(t, fake.Server, WithRequestTimeout(25*time.Millisecond))
	// When
	_, err := client.Create(t.Context(), "", &CreateOptions{Wait: NoWait, Name: "retry-timeout"})
	// Then
	var uncertain *SandboxCreateRequestTimeoutError
	require.ErrorAs(t, err, &uncertain)
	require.ErrorIs(t, err, ErrClientTimeout)
	require.Equal(t, "name:retry-timeout", uncertain.IdempotencyKey)
	require.Len(t, fake.requests(), 1)
}

func TestCreateErrorsCallerCancellationIsNeverUncertain(t *testing.T) {
	for _, route := range createTestCases() {
		for _, phase := range []string{"submission", "accepted wait"} {
			t.Run(route.name+"/"+phase, func(t *testing.T) {
				// Given
				arrived := make(chan struct{})
				blocked := func(_ http.ResponseWriter, r *http.Request) {
					close(arrived)
					<-r.Context().Done()
				}
				post := blocked
				if phase == "accepted wait" {
					post = jsonHandler(t, map[string]string{"id": "sbx", "status": "pending"})
				}
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST " + route.path: post, "GET /sandboxes/sbx": blocked,
				})
				client := newTestClient(t, fake.Server)
				ctx, cancel := context.WithCancel(t.Context())
				defer cancel()
				result := make(chan error, 1)
				// When
				go func() {
					_, err := route.call(ctx, client, &CreateFromSnapshotOptions{Wait: time.Minute})
					result <- err
				}()
				<-arrived
				cancel()
				err := <-result
				// Then
				require.ErrorIs(t, err, context.Canceled)
				var timeout *SandboxCreateRequestTimeoutError
				var connection *SandboxCreateConnectionError
				require.NotErrorAs(t, err, &timeout)
				require.NotErrorAs(t, err, &connection)
			})
		}
	}
}

func TestCreateErrorsCallerDeadlineIsNeverUncertain(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes": func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() },
	})
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithTimeout(t.Context(), 25*time.Millisecond)
	defer cancel()
	// When
	_, err := client.Create(ctx, "", &CreateOptions{Wait: NoWait})
	// Then
	require.ErrorIs(t, err, context.DeadlineExceeded)
	var uncertain *SandboxCreateRequestTimeoutError
	require.NotErrorAs(t, err, &uncertain)
}
