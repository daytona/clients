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

func sandboxMutatorCalls() map[string]func(context.Context, *Sandbox) error {
	match := &IfMatchOptions{IfMatch: `"revision-1"`}
	return map[string]func(context.Context, *Sandbox) error{
		"policy update": func(ctx context.Context, s *Sandbox) error {
			_, err := s.UpdateLifecyclePolicy(ctx, &LifecyclePolicyOptions{Timeout: time.Minute})
			return err
		},
		"idle reset": func(ctx context.Context, s *Sandbox) error { _, err := s.ResetIdleTimer(ctx); return err },
		"labels": func(ctx context.Context, s *Sandbox) error {
			_, err := s.SetLabels(ctx, map[string]string{"name": "test"})
			return err
		},
		"env":         func(ctx context.Context, s *Sandbox) error { return s.UpdateEnv(ctx, nil, nil) },
		"network get": func(ctx context.Context, s *Sandbox) error { _, err := s.GetNetworkPolicy(ctx); return err },
		"network update": func(ctx context.Context, s *Sandbox) error {
			_, err := s.UpdateNetworkPolicy(ctx, NetworkPolicySpec{}, match)
			return err
		},
		"network reset":   func(ctx context.Context, s *Sandbox) error { _, err := s.ResetNetworkPolicy(ctx, match); return err },
		"secrets get":     func(ctx context.Context, s *Sandbox) error { _, err := s.GetSecrets(ctx); return err },
		"secrets replace": func(ctx context.Context, s *Sandbox) error { _, err := s.ReplaceSecrets(ctx, nil, match); return err },
		"rotate":          func(ctx context.Context, s *Sandbox) error { return s.RotateSigningKey(ctx) },
		"access token":    func(ctx context.Context, s *Sandbox) error { _, err := s.CreateAccessToken(ctx, nil); return err },
		"metrics":         func(ctx context.Context, s *Sandbox) error { _, err := s.Metrics(ctx); return err },
	}
}

func TestSandboxPolicyMutatorsHonorCancellation(t *testing.T) {
	for name, call := range sandboxMutatorCalls() {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := newMutatorSandbox(t, fake)
			ctx, cancel := context.WithCancel(t.Context())
			cancel()
			// When
			err := call(ctx, sandbox)
			// Then
			require.ErrorIs(t, err, context.Canceled)
			require.Empty(t, fake.requests())
		})
	}
}

func TestSandboxPolicyMutatorsPreserveAPIErrors(t *testing.T) {
	for name, call := range sandboxMutatorCalls() {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"/": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusConflict)
					writeJSON(t, w, map[string]string{
						"code": "CONFLICT", "source": "API", "message": "sandbox state prevents this operation", "trace_id": "trace-test",
					})
				},
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			err := call(t.Context(), sandbox)
			// Then
			require.ErrorIs(t, err, ErrConflict)
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.Equal(t, "API", failure.Source)
			require.Equal(t, "trace-test", failure.TraceID)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestSandboxPolicyConditionalErrorsAreNotRetried(t *testing.T) {
	for _, tc := range []struct {
		code   string
		status int
		want   error
	}{
		{CodeStaleNetworkPolicyRevision, 412, ErrStaleNetworkPolicyRevision},
		{CodeStaleSecretsRevision, 412, ErrStaleSecretsRevision},
		{CodeIfMatchRequired, 428, ErrIfMatchRequired},
	} {
		t.Run(tc.code, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"/": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(tc.status)
					writeJSON(t, w, map[string]string{"code": tc.code, "source": "API"})
				},
			})
			sandbox := newMutatorSandbox(t, fake)
			calls := sandboxMutatorCalls()
			call := calls["network update"]
			if tc.code == CodeStaleSecretsRevision {
				call = calls["secrets replace"]
			}
			// When
			err := call(t.Context(), sandbox)
			// Then
			require.ErrorIs(t, err, tc.want)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestSandboxPolicyMissingClassReadFailurePreventsMutation(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNotFound) },
	})
	sandbox := newMutatorSandbox(t, fake)
	sandbox.info.Class = ""
	// When
	_, err := sandbox.UpdateLifecyclePolicy(t.Context(), &LifecyclePolicyOptions{Timeout: time.Minute})
	// Then
	require.ErrorIs(t, err, ErrNotFound)
	require.Len(t, fake.requests(), 1)
	require.Equal(t, http.MethodGet, fake.requests()[0].Method)
}
