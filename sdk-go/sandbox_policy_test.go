// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func newMutatorSandbox(t *testing.T, fake *fakeServer) *Sandbox {
	t.Helper()
	client := newTestClient(t, fake.Server)
	persistent := false
	sandbox, err := client.newSandbox(sandboxInit{
		Info:       SandboxInfo{ID: "sbx", Class: SandboxClassLinux, Persistent: &persistent},
		ToolboxURL: fake.Server.URL + "/toolbox",
		Options:    GetSandboxOptions{AccessToken: "caller-" + "credential"},
	})
	require.NoError(t, err)
	return sandbox
}

func TestSandboxPolicyUpdateWireAndCache(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"PATCH /sandboxes/sbx/lifecycle-policy": jsonHandler(t, map[string]any{
			"id": "sbx", "lifecycle_policy": map[string]any{
				"timeout_seconds": 90, "idle_expiry_seconds": 60,
				"on_timeout": "future-action", "on_idle_timeout": "delete",
				"timeout_at": "2026-11-20T15:00:00Z", "idle_timeout_at": "2026-11-20T14:01:00Z",
				"last_activity_at": "2026-11-20T14:00:00.123456Z", "last_activity_source": "future-source",
			},
		}),
	})
	sandbox := newMutatorSandbox(t, fake)

	// When
	policy, err := sandbox.UpdateLifecyclePolicy(t.Context(), &LifecyclePolicyOptions{
		Timeout: 90 * time.Second, IdleExpiry: 5 * time.Second, OnIdleTimeout: LifecycleActionDelete,
	})

	// Then
	require.NoError(t, err)
	require.JSONEq(t, `{"timeout_seconds":90,"idle_expiry_seconds":5,"on_idle_timeout":"delete"}`, string(fake.requests()[0].Body))
	require.Equal(t, LifecycleAction("future-action"), policy.OnTimeout)
	require.Equal(t, "future-source", policy.LastActivitySource)
	require.Equal(t, 123456000, policy.LastActivityAt.Nanosecond())
	require.Equal(t, time.Minute, *policy.IdleExpiry)
	require.Equal(t, policy, sandbox.Info().LifecyclePolicy)
	*policy.Timeout = time.Second
	require.Equal(t, 90*time.Second, *sandbox.Info().LifecyclePolicy.Timeout)
}

func TestSandboxPolicyValidationBeforeRequests(t *testing.T) {
	for name, opts := range map[string]*LifecyclePolicyOptions{
		"nil": nil, "empty": {},
		"disable lifetime":  {Timeout: NoTimeout},
		"negative lifetime": {Timeout: -time.Second},
		"fraction lifetime": {Timeout: time.Second + 1},
		"large lifetime":    {Timeout: 2147483648 * time.Second},
		"negative idle":     {IdleExpiry: -time.Second},
		"fraction idle":     {IdleExpiry: time.Second + 1},
		"large idle":        {IdleExpiry: 2147483648 * time.Second},
		"clear with action": {IdleExpiry: NoTimeout, OnIdleTimeout: LifecycleActionDelete},
		"unknown action":    {OnTimeout: LifecycleAction("hibernate")},
		"linux stop":        {OnTimeout: LifecycleActionStop},
		"linux pause":       {OnIdleTimeout: LifecycleActionPause},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.UpdateLifecyclePolicy(t.Context(), opts)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestSandboxPolicyClassReads(t *testing.T) {
	for _, tc := range []struct {
		name      string
		initial   SandboxClass
		wireClass string
		action    LifecycleAction
		requests  int
		wantError error
	}{
		{"known linux", SandboxClassLinux, "linux", LifecycleActionDelete, 1, nil},
		{"unknown class", "", "linux", LifecycleActionDelete, 2, nil},
		{"persistent mac", SandboxClassMacOS, "macos", LifecycleActionStop, 2, nil},
		{"ephemeral mac", SandboxClassMacOS, "macos", LifecycleActionStop, 1, ErrBadRequest},
		{"missing mac persistence", SandboxClassMacOS, "macos", LifecycleActionStop, 2, nil},
		{"future class", SandboxClassUnknown, "future", LifecycleActionDelete, 1, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			detail := sandboxFixture("sbx")
			detail["class"], detail["persistent"] = tc.wireClass, true
			switch tc.name {
			case "ephemeral mac":
				detail["persistent"] = false
			case "missing mac persistence":
				delete(detail, "persistent")
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx":                    jsonHandler(t, detail),
				"PATCH /sandboxes/sbx/lifecycle-policy": jsonHandler(t, map[string]any{"id": "sbx", "lifecycle_policy": map[string]any{}}),
			})
			sandbox := newMutatorSandbox(t, fake)
			sandbox.info.Class = tc.initial
			sandbox.info.Persistent = nil
			if tc.initial == SandboxClassUnknown {
				sandbox.info.RawClass = "future"
			}
			// When
			_, err := sandbox.UpdateLifecyclePolicy(t.Context(), &LifecyclePolicyOptions{OnTimeout: tc.action})
			// Then
			require.ErrorIs(t, err, tc.wantError)
			require.Len(t, fake.requests(), tc.requests)
		})
	}
}

func TestSandboxPolicyClearIdle(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"PATCH /sandboxes/sbx/lifecycle-policy": jsonHandler(t, map[string]any{"id": "sbx", "lifecycle_policy": map[string]any{}}),
	})
	sandbox := newMutatorSandbox(t, fake)
	// When
	_, err := sandbox.UpdateLifecyclePolicy(t.Context(), &LifecyclePolicyOptions{IdleExpiry: NoTimeout})
	// Then
	require.NoError(t, err)
	require.JSONEq(t, `{"idle_expiry_seconds":-1}`, string(fake.requests()[0].Body))
}

func TestSandboxPolicyCreatePairValidation(t *testing.T) {
	for name, opts := range map[string]*LifecyclePolicyOptions{
		"missing action":   {IdleExpiry: time.Minute},
		"missing duration": {OnIdleTimeout: LifecycleActionDelete},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			requested := *opts
			// When
			_, err := lifecyclePolicyBody(&requested, true)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
