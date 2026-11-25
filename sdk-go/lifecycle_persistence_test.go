// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestLifecyclePolicy_whenPersistenceAndClassVary(t *testing.T) {
	persistent, ephemeral := true, false
	for _, tc := range []struct {
		name        string
		info        SandboxInfo
		stop, pause error
	}{
		{"persistent linux", SandboxInfo{Class: SandboxClassLinux, Persistent: &persistent}, nil, nil},
		{"ephemeral linux", SandboxInfo{Class: SandboxClassLinux, Persistent: &ephemeral}, ErrBadRequest, ErrBadRequest},
		{"persistent mac", SandboxInfo{Class: SandboxClassMacOS, Persistent: &persistent}, nil, ErrBadRequest},
		{"ephemeral mac", SandboxInfo{Class: SandboxClassMacOS, Persistent: &ephemeral}, ErrBadRequest, ErrBadRequest},
		{"persistent gpu", SandboxInfo{Class: SandboxClassUnknown, RawClass: "gpu", Persistent: &persistent}, ErrBadRequest, ErrBadRequest},
		{"inherited linux", SandboxInfo{Class: SandboxClassLinux}, nil, nil},
	} {
		for _, action := range []LifecycleAction{LifecycleActionDelete, LifecycleActionStop, LifecycleActionPause} {
			for _, idle := range []bool{false, true} {
				t.Run(tc.name+"/"+string(action)+"/idle="+strconv.FormatBool(idle), func(t *testing.T) {
					// Given the same support matrix for either policy action.
					options := &LifecyclePolicyOptions{OnTimeout: action}
					if idle {
						options = &LifecyclePolicyOptions{OnIdleTimeout: action}
					}
					// When checking class and known persistence.
					err := lifecyclePolicyClassSupport(options, tc.info)
					// Then delete works everywhere; stop and pause follow their matrix.
					var want error
					switch action {
					case LifecycleActionStop:
						want = tc.stop
					case LifecycleActionPause:
						want = tc.pause
					case LifecycleActionDelete:
					}
					require.ErrorIs(t, err, want)
				})
			}
		}
	}
}

func TestCreate_whenPersistentLifecyclePolicyIsRequested(t *testing.T) {
	persistent := true
	for _, action := range []LifecycleAction{LifecycleActionStop, LifecycleActionPause} {
		t.Run(string(action), func(t *testing.T) {
			// Given a persistent Linux create.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes": jsonHandler(t, map[string]string{"id": "created"}),
			})
			client := newTestClient(t, fake.Server)
			// When requesting a persistent-only policy.
			_, err := client.Create(t.Context(), "", &CreateOptions{
				Persistent: &persistent, Wait: NoWait, LifecyclePolicy: &LifecyclePolicyOptions{OnTimeout: action},
			})
			// Then validation allows the request.
			require.NoError(t, err)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestLifecyclePolicy_whenLinuxPersistenceNeedsRefresh(t *testing.T) {
	// Given a Linux handle whose persistence was not in its create acknowledgement.
	detail := sandboxFixture("sbx")
	detail["persistent"] = true
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx": jsonHandler(t, detail),
		"PATCH /sandboxes/sbx/lifecycle-policy": jsonHandler(t, map[string]any{
			"id": "sbx", "lifecycle_policy": map[string]string{"on_timeout": "pause"},
		}),
	})
	sandbox := newMutatorSandbox(t, fake)
	sandbox.info.Persistent = nil
	// When updating to a persistence-dependent policy action.
	_, err := sandbox.UpdateLifecyclePolicy(t.Context(), &LifecyclePolicyOptions{OnTimeout: LifecycleActionPause})
	// Then the fresh observation permits the update.
	require.NoError(t, err)
	require.Len(t, fake.requests(), 2)
	require.Equal(t, http.MethodGet, fake.requests()[0].Method)
	require.Equal(t, http.MethodPatch, fake.requests()[1].Method)
}
