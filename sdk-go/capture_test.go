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

func TestCaptureImage_whenOptionsAreSent(t *testing.T) {
	for _, tt := range []struct {
		name string
		opts *CaptureImageOptions
		body string
	}{
		{"defaults", nil, `{}`},
		{"named", &CaptureImageOptions{Name: "capture"}, `{"name":"capture"}`},
		{"expiry", &CaptureImageOptions{IdleExpiry: 30 * time.Second}, `{"idle_expiry_seconds":30}`},
		{"maximum", &CaptureImageOptions{IdleExpiry: WaitForever}, `{"idle_expiry_seconds":-1}`},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given a capture acknowledgement followed by its pending observation.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/sbx/images": jsonHandler(t, map[string]string{"id": "img/one"}),
				"GET /images/{id}":           jsonHandler(t, imageFixture("img/one", "pending")),
			})
			client := newTestClient(t, fake.Server)
			sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
			require.NoError(t, err)
			// When capturing without waiting for readiness.
			image, err := sandbox.CaptureImage(t.Context(), tt.opts)
			// Then only the acknowledgement ID is followed and options retain wire units.
			require.NoError(t, err)
			require.Equal(t, "img/one", image.ID)
			require.Equal(t, ImageStatusPending, image.Status)
			requests := fake.requests()
			require.Len(t, requests, 2)
			require.JSONEq(t, tt.body, string(requests[0].Body))
			require.Equal(t, "/images/img%2Fone", requests[1].Path)
			require.Empty(t, requests[1].Query)
		})
	}
}

func TestCreateSnapshot_whenAcknowledged(t *testing.T) {
	// Given a pending snapshot whose ID contains a path separator.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/snapshots": jsonHandler(t, map[string]string{"id": "snap/one"}),
		"GET /snapshots/{id}":           jsonHandler(t, snapshotFixture("snap/one", "creating")),
	})
	client := newTestClient(t, fake.Server)
	sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
	require.NoError(t, err)
	// When capturing memory and disk.
	snapshot, err := sandbox.CreateSnapshot(t.Context(), "saved")
	// Then the API-owned class is read through one escaped, ordinary lookup.
	require.NoError(t, err)
	require.Equal(t, "snap/one", snapshot.ID)
	require.Equal(t, SnapshotStatusCreating, snapshot.Status)
	require.Equal(t, SnapshotClassLinux, snapshot.Class)
	requests := fake.requests()
	require.Len(t, requests, 2)
	require.JSONEq(t, `{"name":"saved"}`, string(requests[0].Body))
	require.Equal(t, "/snapshots/snap%2Fone", requests[1].Path)
	require.Empty(t, requests[1].Query)
}

func captureResourceCases() []struct {
	name string
	call func(context.Context, *Sandbox) error
} {
	return []struct {
		name string
		call func(context.Context, *Sandbox) error
	}{
		{"images", func(ctx context.Context, sandbox *Sandbox) error {
			_, err := sandbox.CaptureImage(ctx, nil)
			return err
		}},
		{"snapshots", func(ctx context.Context, sandbox *Sandbox) error {
			_, err := sandbox.CreateSnapshot(ctx, "saved")
			return err
		}},
	}
}

func TestCapture_whenAcknowledgementIDIsMalformed(t *testing.T) {
	for _, resource := range captureResourceCases() {
		t.Run(resource.name, func(t *testing.T) {
			for _, body := range []any{
				nil,
				[]any{},
				map[string]any{},
				map[string]any{"id": nil},
				map[string]any{"id": 7},
				map[string]any{"id": ""},
				map[string]any{"id": " "},
				map[string]any{"id": "."},
				map[string]any{"id": ".."},
			} {
				t.Run("invalid id", func(t *testing.T) {
					// Given an acknowledgement with no safe follow-up identity.
					fake := newFakeServer(t, map[string]http.HandlerFunc{
						"POST /sandboxes/sbx/" + resource.name: jsonHandler(t, body),
					})
					client := newTestClient(t, fake.Server)
					sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
					require.NoError(t, err)
					// When capturing.
					err = resource.call(t.Context(), sandbox)
					// Then validation happens before any follow-up GET.
					require.ErrorIs(t, err, ErrBadRequest)
					require.Len(t, fake.requests(), 1)
				})
			}
		})
	}
}

func TestCapture_whenServerRejectsEitherPhase(t *testing.T) {
	for _, resource := range captureResourceCases() {
		for _, phase := range []string{"submit", "lookup"} {
			t.Run(resource.name+"/"+phase, func(t *testing.T) {
				// Given a typed error during capture or its initial lookup.
				reject := func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(403)
					writeJSON(t, w, map[string]string{"code": "FORBIDDEN", "source": "API"})
				}
				post := jsonHandler(t, map[string]string{"id": "artifact"})
				if phase == "submit" {
					post = reject
				}
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST /sandboxes/sbx/" + resource.name: post,
					"GET /" + resource.name + "/artifact":  reject,
				})
				client := newTestClient(t, fake.Server)
				sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
				require.NoError(t, err)
				// When capturing.
				err = resource.call(t.Context(), sandbox)
				// Then failure is preserved rather than returning an empty handle.
				require.ErrorIs(t, err, ErrForbidden)
				var detail *Error
				require.ErrorAs(t, err, &detail)
				require.Equal(t, "API", detail.Source)
				want := 1
				if phase == "lookup" {
					want = 2
				}
				require.Len(t, fake.requests(), want)
			})
		}
	}
}

func TestCaptureImage_whenOptionsAreInvalid(t *testing.T) {
	for _, options := range []CaptureImageOptions{
		{Name: " "}, {Name: "\xff"}, {IdleExpiry: time.Nanosecond}, {IdleExpiry: -time.Second},
	} {
		t.Run("invalid options", func(t *testing.T) {
			// Given invalid naming or retention before a capture.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
			require.NoError(t, err)
			// When capturing.
			_, err = sandbox.CaptureImage(t.Context(), &options)
			// Then invalid options never reach the API.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestCaptureImage_whenDiskIsNotReady(t *testing.T) {
	// Given an API rejection of a capture whose source disk is not ready.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/images": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(409)
			writeJSON(t, w, map[string]string{"code": "DISK_NOT_READY", "source": "API"})
		},
	})
	client := newTestClient(t, fake.Server)
	sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
	require.NoError(t, err)
	// When capturing without a local state or class pre-check.
	_, err = sandbox.CaptureImage(t.Context(), nil)
	// Then the server's retryable condition remains identifiable.
	require.ErrorIs(t, err, ErrDiskNotReady)
	require.Len(t, fake.requests(), 1)
}
