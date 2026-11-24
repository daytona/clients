// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestSandboxPolicyResetPreservesNewestConcurrentClock(t *testing.T) {
	// Given
	firstArrived, releaseFirst := make(chan struct{}), make(chan struct{})
	var calls atomic.Int32
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/reset-idle-timer": func(w http.ResponseWriter, _ *http.Request) {
			stamp := "2026-11-20T14:00:00.000500Z"
			if calls.Add(1) == 1 {
				close(firstArrived)
				<-releaseFirst
				stamp = "2026-11-20T14:00:00Z"
			}
			writeJSON(t, w, map[string]any{"id": "sbx", "lifecycle_policy": map[string]any{"last_activity_at": stamp}})
		},
	})
	sandbox := newMutatorSandbox(t, fake)
	type result struct {
		policy *LifecyclePolicy
		err    error
	}
	earlier := make(chan result, 1)

	// When
	go func() {
		policy, err := sandbox.ResetIdleTimer(t.Context())
		earlier <- result{policy, err}
	}()
	<-firstArrived
	later, laterErr := sandbox.ResetIdleTimer(t.Context())
	close(releaseFirst)
	first := <-earlier

	// Then
	require.NoError(t, first.err)
	require.NoError(t, laterErr)
	require.True(t, first.policy.LastActivityAt.Before(later.LastActivityAt))
	require.Equal(t, later.LastActivityAt, sandbox.Info().LifecyclePolicy.LastActivityAt)
	for _, request := range fake.requests() {
		require.Empty(t, request.Body)
	}
}

func TestSandboxPolicyResetClocklessEcho(t *testing.T) {
	for _, cached := range []bool{false, true} {
		t.Run(map[bool]string{false: "empty cache", true: "valid cache"}[cached], func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/sbx/reset-idle-timer": jsonHandler(t, map[string]any{
					"id": "sbx", "lifecycle_policy": map[string]any{"last_activity_at": nil, "last_activity_source": "api"},
				}),
			})
			sandbox := newMutatorSandbox(t, fake)
			if cached {
				sandbox.info.LifecyclePolicy = &LifecyclePolicy{LastActivityAt: time.Unix(1234, 500), LastActivitySource: "ssh"}
			}
			// When
			policy, err := sandbox.ResetIdleTimer(t.Context())
			// Then
			require.NoError(t, err)
			require.True(t, policy.LastActivityAt.IsZero())
			if cached {
				require.Equal(t, "ssh", sandbox.Info().LifecyclePolicy.LastActivitySource)
			} else {
				require.Equal(t, "api", sandbox.Info().LifecyclePolicy.LastActivitySource)
			}
		})
	}
}

func TestSandboxPolicyRejectsMalformedEcho(t *testing.T) {
	for name, body := range map[string]any{
		"array":         []string{},
		"id absent":     map[string]any{"lifecycle_policy": map[string]any{}},
		"id wrong":      map[string]any{"id": 1, "lifecycle_policy": map[string]any{}},
		"policy absent": map[string]any{"id": "sbx"},
		"policy null":   map[string]any{"id": "sbx", "lifecycle_policy": nil},
		"policy array":  map[string]any{"id": "sbx", "lifecycle_policy": []string{}},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes/sbx/reset-idle-timer": jsonHandler(t, body)})
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.ResetIdleTimer(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Nil(t, sandbox.Info().LifecyclePolicy)
		})
	}
}

func TestSandboxPolicyRejectsMalformedPolicyFields(t *testing.T) {
	for field, invalid := range map[string][]any{
		"timeout_seconds":     {true, 1.2, int64(2147483648), int64(-2147483649)},
		"idle_expiry_seconds": {"1", 1.2, int64(2147483648)},
		"on_timeout":          {1}, "on_idle_timeout": {false}, "last_activity_source": {[]string{}},
		"timeout_at": {"invalid"}, "idle_timeout_at": {1}, "last_activity_at": {"2026-99-20T00:00:00Z"},
	} {
		for index, value := range invalid {
			t.Run(field+"/"+string(rune('a'+index)), func(t *testing.T) {
				// Given
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST /sandboxes/sbx/reset-idle-timer": jsonHandler(t, map[string]any{
						"id": "sbx", "lifecycle_policy": map[string]any{field: value},
					}),
				})
				sandbox := newMutatorSandbox(t, fake)
				// When
				_, err := sandbox.ResetIdleTimer(t.Context())
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.Nil(t, sandbox.Info().LifecyclePolicy)
			})
		}
	}
}
