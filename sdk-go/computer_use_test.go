// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func computerUseTestSandbox(t *testing.T, handler http.HandlerFunc) (*Sandbox, *fakeServer) {
	t.Helper()
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx": jsonHandler(t, sandboxFixture("sbx")),
		"/computeruse/":      handler,
	})
	client := newTestClient(t, fake.Server)
	sandbox, err := client.Get(t.Context(), "sbx", &GetSandboxOptions{
		ToolboxURL: fake.Server.URL, AccessToken: "desktop-fixture-token",
	})
	require.NoError(t, err)
	return sandbox, fake
}

type desktopWireCase struct {
	name     string
	method   string
	path     string
	body     string
	response string
	call     func(context.Context, *ComputerUse) error
}

func runDesktopWireCases(t *testing.T, cases []desktopWireCase) {
	t.Helper()
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			sandbox, fake := computerUseTestSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_, err := w.Write([]byte(tc.response))
				if err != nil {
					t.Errorf("write desktop response: %v", err)
				}
			})
			// When
			err := tc.call(t.Context(), sandbox.ComputerUse())
			// Then
			require.NoError(t, err)
			requests := fake.requests()
			require.Len(t, requests, 2)
			got := requests[1]
			require.Equal(t, tc.method, got.Method)
			require.Equal(t, tc.path, got.Path)
			require.Equal(t, "Bearer desktop-fixture-token", got.Header.Get("Authorization"))
			if tc.body == "" {
				require.Empty(t, got.Body)
			} else {
				require.JSONEq(t, tc.body, string(got.Body))
			}
		})
	}
}

func TestComputerUseLifecycle_whenRequested(t *testing.T) {
	lifecycle := `{"message":"changed","status":{}}`
	restart := `{"message":"restarted","status":"active","desktop":"ready","future":42}`
	runDesktopWireCases(t, []desktopWireCase{
		{
			"start defaults", "POST", "/computeruse/start", "", lifecycle,
			func(ctx context.Context, c *ComputerUse) error {
				got, err := c.Start(ctx, nil)
				if err == nil {
					require.Equal(t, "changed", got.Message)
					require.Empty(t, got.Status)
				}
				return err
			},
		},
		{
			"start resolution", "POST", "/computeruse/start", `{"resolution":{"width":1920,"height":1080}}`, lifecycle,
			func(ctx context.Context, c *ComputerUse) error {
				_, err := c.Start(ctx, &ComputerUseStartOptions{Resolution: &ComputerUseResolution{Width: 1920, Height: 1080}})
				return err
			},
		},
		{
			"stop", "POST", "/computeruse/stop", "", lifecycle,
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Stop(ctx); return err },
		},
		{
			"restart defaults", "POST", "/computeruse/restart", "", restart,
			func(ctx context.Context, c *ComputerUse) error {
				got, err := c.Restart(ctx, nil)
				if err == nil {
					require.Equal(t, ComputerUseDesktopReady, got.Desktop)
					require.JSONEq(t, "42", string(got.ExtraFields["future"]))
				}
				return err
			},
		},
		{
			"restart resolution", "POST", "/computeruse/restart", `{"resolution":{"width":1024,"height":768}}`, restart,
			func(ctx context.Context, c *ComputerUse) error {
				_, err := c.Restart(ctx, &ComputerUseStartOptions{Resolution: &ComputerUseResolution{Width: 1024, Height: 768}})
				return err
			},
		},
	})
}

func TestComputerUseStatus_whenServerReportsPairs(t *testing.T) {
	cases := []struct {
		name, rawStatus, rawDesktop string
		status                      ComputerUseActivityStatus
		desktop                     ComputerUseDesktopState
	}{
		{"not started", "inactive", "not_started", ComputerUseActivityInactive, ComputerUseDesktopNotStarted},
		{"starting", "inactive", "starting", ComputerUseActivityInactive, ComputerUseDesktopStarting},
		{"ready", "active", "ready", ComputerUseActivityActive, ComputerUseDesktopReady},
		{"new status", "degraded", "ready", ComputerUseActivityUnknown, ComputerUseDesktopUnknown},
		{"new desktop", "inactive", "rebooting", ComputerUseActivityUnknown, ComputerUseDesktopUnknown},
		{"new pair", "active", "starting", ComputerUseActivityUnknown, ComputerUseDesktopUnknown},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, map[string]string{
				"status": tc.rawStatus, "desktop": tc.rawDesktop,
			}))
			// When
			got, err := sandbox.ComputerUse().Status(t.Context())
			// Then
			require.NoError(t, err)
			require.Equal(t, tc.status, got.Status)
			require.Equal(t, tc.desktop, got.Desktop)
			require.Equal(t, tc.rawStatus, got.RawStatus)
			require.Equal(t, tc.rawDesktop, got.RawDesktop)
			require.Nil(t, got.Cause)
		})
	}
}

func TestComputerUseStatus_whenFailedPreservesCause(t *testing.T) {
	for _, code := range []string{
		"desktop_exited", "addon_crashed", "start_failed", "start_interrupted",
		"session_ended", "session_replaced", "state_corrupt", "state_persistence_failed", "future",
	} {
		t.Run(code, func(t *testing.T) {
			// Given
			sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, map[string]any{
				"status": "inactive", "desktop": "failed", "future": 7,
				"cause": map[string]any{"code": code, "message": "failure", "detail": true},
			}))
			// When
			got, err := sandbox.ComputerUse().Status(t.Context())
			// Then
			require.NoError(t, err)
			require.Equal(t, ComputerUseDesktopFailed, got.Desktop)
			require.Equal(t, code, got.Cause.RawCode)
			want := ComputerUseDesktopFailureCode(code)
			if code == "future" {
				want = ComputerUseFailureUnknown
			}
			require.Equal(t, want, got.Cause.Code)
			require.JSONEq(t, "true", string(got.Cause.ExtraFields["detail"]))
			require.JSONEq(t, "7", string(got.ExtraFields["future"]))
		})
	}
}
