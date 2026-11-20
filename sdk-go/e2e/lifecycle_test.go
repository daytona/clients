// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	daytona "go.daytona.com/sdk/v2"
)

func TestLifecycleCloud(t *testing.T) {
	client := newClient(t)
	ctx := testContext(t)

	// Given: a default-image sandbox, cleaned up even after a failed assertion.
	sandbox, err := client.Create(ctx, "", &daytona.CreateOptions{
		Name: uniqueName(t, "gosdk-lifecycle-default"), Labels: map[string]string{labelKey: labelValue},
	})
	if sandbox != nil {
		lifecycleCleanup(ctx, t, sandbox)
	}
	require.NoError(t, err)
	require.Equal(t, daytona.SandboxStateStarted, sandbox.State())
	require.NoError(t, sandbox.Refresh(ctx))

	t.Run("labels", func(t *testing.T) {
		// When
		labels, labelErr := sandbox.SetLabels(ctx, map[string]string{labelKey: labelValue, "stage": "lifecycle"})
		// Then
		require.NoError(t, labelErr)
		require.Equal(t, "lifecycle", labels["stage"])
		require.Equal(t, sandbox.Name(), labels["name"])
	})
	t.Run("environment", func(t *testing.T) {
		// When/Then: the real toolbox accepts set and unset operations.
		require.NoError(t, sandbox.UpdateEnv(ctx, map[string]string{"GOSDK_LIFECYCLE": "set"}, nil))
		require.NoError(t, sandbox.UpdateEnv(ctx, nil, &daytona.UpdateEnvOptions{Unset: []string{"GOSDK_LIFECYCLE"}}))
	})
	t.Run("policy", func(t *testing.T) {
		// When
		policy, policyErr := sandbox.UpdateLifecyclePolicy(ctx, &daytona.LifecyclePolicyOptions{
			Timeout: 30 * time.Minute, OnTimeout: daytona.LifecycleActionDelete,
			IdleExpiry: 10 * time.Minute, OnIdleTimeout: daytona.LifecycleActionDelete,
		})
		// Then
		require.NoError(t, policyErr)
		require.NotNil(t, policy.Timeout)
		require.Equal(t, 30*time.Minute, *policy.Timeout)
	})
	t.Run("idle_reset", func(t *testing.T) {
		// When
		policy, resetErr := sandbox.ResetIdleTimer(ctx)
		// Then
		require.NoError(t, resetErr)
		require.False(t, policy.LastActivityAt.IsZero())
	})
	t.Run("idle_clear", func(t *testing.T) {
		// When
		policy, clearErr := sandbox.UpdateLifecyclePolicy(ctx, &daytona.LifecyclePolicyOptions{IdleExpiry: daytona.NoTimeout})
		// Then
		require.NoError(t, clearErr)
		require.True(t, policy.IdleTimeoutAt.IsZero())
		require.Empty(t, policy.OnIdleTimeout)
	})
	t.Run("metrics", func(t *testing.T) {
		// When
		metrics, metricsErr := sandbox.Metrics(ctx)
		// Then
		require.NoError(t, metricsErr)
		require.Positive(t, metrics.MemoryTotalBytes)
	})
	t.Run("access_token", func(t *testing.T) {
		// When
		token, tokenErr := sandbox.CreateAccessToken(ctx, &daytona.CreateAccessTokenOptions{TTL: time.Minute})
		// Then: never include credential contents in diagnostics.
		require.NoError(t, tokenErr)
		require.NotEmpty(t, token.AccessToken)
		require.False(t, token.ExpiresAt.IsZero())
	})
	t.Run("signing_key", func(t *testing.T) {
		// When/Then
		require.NoError(t, sandbox.RotateSigningKey(ctx))
	})
	t.Run("network_policy", func(t *testing.T) {
		// Given
		policy, policyErr := sandbox.GetNetworkPolicy(ctx)
		require.NoError(t, policyErr)
		originalETag := policy.ETag
		blockAll := false
		// When
		updated, updateErr := sandbox.UpdateNetworkPolicy(ctx,
			daytona.NetworkPolicySpec{BlockAll: &blockAll},
			&daytona.IfMatchOptions{IfMatch: originalETag})
		// Then: asynchronous application must settle before another mutation.
		require.NoError(t, updateErr)
		eventually(ctx, t, 30*time.Second, func() (bool, error) {
			latest, readErr := sandbox.GetNetworkPolicy(ctx)
			if readErr != nil {
				return false, readErr
			}
			return latest.AppliedRevision != nil && *latest.AppliedRevision == updated.DesiredRevision, nil
		})
		t.Run("stale_revision", func(t *testing.T) {
			// When
			_, staleErr := sandbox.ResetNetworkPolicy(ctx, &daytona.IfMatchOptions{IfMatch: originalETag})
			// Then: a real server error preserves its typed code.
			require.ErrorIs(t, staleErr, daytona.ErrStaleNetworkPolicyRevision)
		})
		t.Run("reset", func(t *testing.T) {
			// When
			reset, resetErr := sandbox.ResetNetworkPolicy(ctx, &daytona.IfMatchOptions{IfMatch: updated.ETag})
			// Then
			require.NoError(t, resetErr)
			require.Nil(t, reset.Desired)
		})
	})
	t.Run("secrets", func(t *testing.T) {
		// Given
		secrets, readErr := sandbox.GetSecrets(ctx)
		require.NoError(t, readErr)
		// When: replacing the empty binding set needs no project secret.
		applied, replaceErr := sandbox.ReplaceSecrets(ctx, nil, &daytona.IfMatchOptions{IfMatch: secrets.ETag})
		// Then
		require.NoError(t, replaceErr)
		require.Empty(t, applied.Secrets)
	})
	t.Run("pause_resume", func(t *testing.T) {
		lifecycleRequirePersistence(t, sandbox)
		// When/Then: unsupported platforms decide at the API, not in the SDK.
		if pauseErr := sandbox.Pause(ctx, nil); errors.Is(pauseErr, daytona.ErrNotSupportedForClass) {
			t.Skip("platform does not support pause for this sandbox class")
		} else {
			require.NoError(t, pauseErr)
		}
		require.Equal(t, daytona.SandboxStatePaused, sandbox.State())
		require.NoError(t, sandbox.Resume(ctx, nil))
		require.Equal(t, daytona.SandboxStateStarted, sandbox.State())
	})
	t.Run("fork", func(t *testing.T) {
		lifecycleRequirePersistence(t, sandbox)
		// When
		fork, forkErr := sandbox.Fork(ctx, &daytona.ForkOptions{
			Name: uniqueName(t, "gosdk-lifecycle-fork"),
		})
		if fork != nil {
			lifecycleCleanup(ctx, t, fork)
		}
		// Then
		require.NoError(t, forkErr)
		require.Equal(t, daytona.SandboxStateStarted, fork.State())
		require.NotEqual(t, sandbox.ID(), fork.ID())
	})
	t.Run("stop_start", func(t *testing.T) {
		lifecycleRequirePersistence(t, sandbox)
		// When/Then
		require.NoError(t, sandbox.Stop(ctx, nil))
		require.Equal(t, daytona.SandboxStateStopped, sandbox.State())
		require.NoError(t, sandbox.Start(ctx, nil))
		require.Equal(t, daytona.SandboxStateStarted, sandbox.State())
	})
	t.Run("explicit_image", func(t *testing.T) {
		// Given: the default-image handle reports the exact reusable image ID.
		info := sandbox.Info()
		require.NotNil(t, info.Image)
		// When
		explicit, createErr := client.Create(ctx, info.Image.ID, &daytona.CreateOptions{
			Name: uniqueName(t, "gosdk-lifecycle-image"), Labels: map[string]string{labelKey: labelValue},
		})
		if explicit != nil {
			lifecycleCleanup(ctx, t, explicit)
		}
		// Then
		require.NoError(t, createErr)
		require.Equal(t, daytona.SandboxStateStarted, explicit.State())
	})
}

func lifecycleRequirePersistence(t *testing.T, sandbox *daytona.Sandbox) {
	t.Helper()
	if persistent := sandbox.Info().Persistent; persistent != nil && !*persistent {
		t.Skip("this deployment's sandbox is non-persistent; pause, fork and warm stop are unavailable")
	}
}

func lifecycleCleanup(ctx context.Context, t *testing.T, sandbox *daytona.Sandbox) {
	t.Helper()
	t.Cleanup(func() {
		// Cleanup must survive cancellation/failure of the operation's context.
		cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Minute)
		defer cancel()
		err := sandbox.Delete(cleanupCtx, nil)
		if !errors.Is(err, daytona.ErrNotFound) {
			require.NoError(t, err)
		}
	})
}
