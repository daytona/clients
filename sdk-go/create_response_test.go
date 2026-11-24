// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

type createTestCase struct {
	name  string
	path  string
	class SandboxClass
	call  func(context.Context, *Client, *CreateFromSnapshotOptions) (*Sandbox, error)
}

func createTestCases() []createTestCase {
	return []createTestCase{
		{"linux", "/sandboxes", SandboxClassLinux, func(ctx context.Context, c *Client, o *CreateFromSnapshotOptions) (*Sandbox, error) {
			return c.Create(ctx, "img_source", &CreateOptions{
				Wait: o.Wait, Name: o.Name, IdempotencyKey: o.IdempotencyKey, Region: o.Region,
			})
		}},
		{"snapshot", "/sandboxes/from-snapshot", SandboxClassLinux, func(ctx context.Context, c *Client, o *CreateFromSnapshotOptions) (*Sandbox, error) {
			return c.CreateFromSnapshot(ctx, "snap_source", o)
		}},
		{"macos", "/sandboxes/macos", SandboxClassMacOS, func(ctx context.Context, c *Client, o *CreateFromSnapshotOptions) (*Sandbox, error) {
			return c.CreateMacOS(ctx, "img_source", &CreateMacOSOptions{
				Wait: o.Wait, Name: o.Name, IdempotencyKey: o.IdempotencyKey, Region: o.Region,
			})
		}},
	}
}

func TestCreateResponsePreservesOnlyObservedMetadata(t *testing.T) {
	for _, route := range createTestCases() {
		t.Run(route.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST " + route.path: jsonHandler(t, map[string]any{
					"id": "sbx", "status": "future-state", "class": "future-class", "name": "observed",
					"reservation_id": "res-1", "image": map[string]string{"id": "img_resolved", "name": "resolved"},
					"labels": map[string]string{"ignored": "create response"}, "cpu": 99,
					"access_token": "create-" + "credential", "access_token_expires_at": "2030-01-01T00:00:00Z",
					"toolbox_proxy_url": "https://toolbox.example.com",
				}),
			})
			client := newTestClient(t, fake.Server)
			// When
			sandbox, err := route.call(t.Context(), client, &CreateFromSnapshotOptions{
				Wait: NoWait, Name: "requested", Region: "region-a",
			})
			// Then
			require.NoError(t, err)
			info := sandbox.Info()
			require.Equal(t, SandboxStateUnknown, info.State)
			require.Equal(t, "future-state", info.RawState)
			require.Equal(t, route.class, info.Class)
			require.Equal(t, "observed", info.Name)
			require.Equal(t, "res-1", info.ReservationID)
			require.Equal(t, &ImageRef{ID: "img_resolved", Name: "resolved"}, info.Image)
			require.Equal(t, "region-a", info.Region)
			require.Nil(t, info.Labels)
			require.Zero(t, info.Resources.CPU)
			require.Equal(t, "https://toolbox.example.com/sbx", sandbox.toolboxURL)
			require.Equal(t, "create-"+"credential", sandbox.auth.cache.snapshot().token)
			require.Equal(t, time.Date(2030, 1, 1, 0, 0, 0, 0, time.UTC), sandbox.auth.cache.snapshot().expiresAt)
			require.True(t, sandbox.auth.mintable)
			if route.name == "snapshot" {
				require.Equal(t, &SnapshotRef{ID: "snap_source"}, info.Snapshot)
			}
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestCreateResponseOptionalMistypesDoNotLoseHandle(t *testing.T) {
	for _, route := range createTestCases() {
		t.Run(route.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST " + route.path: jsonHandler(t, map[string]any{
					"id": "sbx", "status": true, "name": []string{}, "image": "invalid",
					"reservation_id": false, "access_token": " padded", "access_token_expires_at": "invalid",
					"toolbox_proxy_url": 1, "wait_cap_seconds": "many",
				}),
			})
			client := newTestClient(t, fake.Server)
			// When
			sandbox, err := route.call(t.Context(), client, &CreateFromSnapshotOptions{Wait: NoWait})
			// Then
			require.NoError(t, err)
			require.Equal(t, SandboxStateUnknown, sandbox.State())
			require.Empty(t, sandbox.Name())
			require.Empty(t, sandbox.Info().ReservationID)
			require.Empty(t, sandbox.toolboxURL)
			require.Empty(t, sandbox.auth.cache.snapshot().token)
			require.True(t, sandbox.auth.cache.snapshot().expiresAt.IsZero())
			if route.name != "snapshot" {
				require.Equal(t, &ImageRef{ID: "img_source"}, sandbox.Info().Image)
			}
		})
	}
}

func TestCreateResponseRequiresUsableID(t *testing.T) {
	for name, body := range map[string]any{
		"null": nil, "array": []string{}, "missing": map[string]any{},
		"null id": map[string]any{"id": nil}, "number": map[string]any{"id": 1},
		"empty": map[string]any{"id": ""}, "blank": map[string]any{"id": " "},
		"dot": map[string]any{"id": "."}, "parent": map[string]any{"id": ".."},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes": jsonHandler(t, body)})
			client := newTestClient(t, fake.Server)
			// When
			sandbox, err := client.Create(t.Context(), "", nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Nil(t, sandbox)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestCreateResponseEncodesAcceptedIDBeforeWait(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes":     jsonHandler(t, map[string]string{"id": "part/one", "status": "pending"}),
		"GET /sandboxes/{id}": jsonHandler(t, sandboxFixture("part/one")),
	})
	client := newTestClient(t, fake.Server)
	// When
	sandbox, err := client.Create(t.Context(), "image-name", nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, "part/one", sandbox.ID())
	require.Equal(t, "/sandboxes/part%2Fone", fake.requests()[1].Path)
}

func TestCreateResponseImageNameIsNotAnObservedID(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes": jsonHandler(t, map[string]string{"id": "sbx"})})
	client := newTestClient(t, fake.Server)
	// When
	sandbox, err := client.Create(t.Context(), "image-name", &CreateOptions{Wait: NoWait})
	// Then
	require.NoError(t, err)
	require.Nil(t, sandbox.Info().Image)
}

func TestCreateResponseCarriesTheLifecyclePolicy(t *testing.T) {
	for _, route := range createTestCases() {
		t.Run(route.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST " + route.path: jsonHandler(t, map[string]any{
					"id": "sbx", "status": "pending", "toolbox_proxy_url": "https://toolbox.example.com",
					"lifecycle_policy": map[string]any{
						"timeout_seconds": 120, "on_timeout": "delete",
						"timeout_at":          "2026-11-17T10:02:00.123456Z",
						"idle_expiry_seconds": nil, "on_idle_timeout": nil, "idle_timeout_at": nil,
						"wake_on_request":      false,
						"last_activity_at":     "2026-11-17T10:00:00.123456Z",
						"last_activity_source": "api",
					},
				}),
			})
			client := newTestClient(t, fake.Server)
			// When
			sandbox, err := route.call(t.Context(), client, &CreateFromSnapshotOptions{Wait: NoWait})
			// Then
			require.NoError(t, err)
			policy := sandbox.Info().LifecyclePolicy
			require.NotNil(t, policy)
			require.Equal(t, 120*time.Second, *policy.Timeout)
			require.Equal(t, LifecycleAction("delete"), policy.OnTimeout)
			require.Equal(t, time.Date(2026, 11, 17, 10, 2, 0, 123456000, time.UTC), policy.TimeoutAt)
			encoded, err := json.Marshal(sandbox.ToJSON())
			require.NoError(t, err)
			require.Contains(t, string(encoded), `"timeoutAt":"2026-11-17T10:02:00.123456Z"`)
			// Read from the create response alone: no detail read.
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestCreateResponseMalformedLifecyclePolicyIsAbsent(t *testing.T) {
	valid := map[string]any{"timeout_seconds": 120, "on_timeout": "delete", "timeout_at": "2026-11-17T10:02:00Z"}
	with := func(key string, value any) map[string]any {
		out := map[string]any{}
		for k, v := range valid {
			out[k] = v
		}
		out[key] = value
		return out
	}
	cases := map[string]any{
		"null": nil, "string": "delete", "array": []any{},
		"bad timestamp":      with("timeout_at", "not a timestamp"),
		"mistyped integer":   with("timeout_seconds", map[string]any{}),
		"out of int32 range": with("timeout_seconds", int64(1)<<31),
		"mistyped action":    with("on_timeout", 5),
	}
	for _, route := range createTestCases() {
		for name, lifecycle := range cases {
			t.Run(route.name+"/"+name, func(t *testing.T) {
				// Given
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST " + route.path: jsonHandler(t, map[string]any{
						"id": "sbx", "status": "pending", "lifecycle_policy": lifecycle,
					}),
				})
				client := newTestClient(t, fake.Server)
				// When
				sandbox, err := route.call(t.Context(), client, &CreateFromSnapshotOptions{Wait: NoWait})
				// Then: the sandbox exists, so the policy is absent, never an error.
				require.NoError(t, err)
				require.Equal(t, "sbx", sandbox.ID())
				require.Nil(t, sandbox.Info().LifecyclePolicy)
				require.Len(t, fake.requests(), 1)
			})
		}
	}
}
