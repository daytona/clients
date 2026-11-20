// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestSandboxVolumesPull_whenWaitIsSpecified(t *testing.T) {
	for _, test := range []struct {
		name string
		opts *PullVolumeOptions
		body string
	}{
		{"nil", nil, `{"requestId":"request_1"}`},
		{"default", &PullVolumeOptions{}, `{"requestId":"request_1"}`},
		{"check", &PullVolumeOptions{Timeout: NoWait}, `{"requestId":"request_1","waitMs":0}`},
		{"maximum", &PullVolumeOptions{Timeout: 30 * time.Second}, `{"requestId":"request_1","waitMs":30000}`},
		{"floor", &PullVolumeOptions{Timeout: 1234500 * time.Microsecond}, `{"requestId":"request_1","waitMs":1234}`},
		{"minimum", &PullVolumeOptions{Timeout: time.Nanosecond}, `{"requestId":"request_1","waitMs":1}`},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /volumes/volume_1/pull": func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusAccepted)
				writeJSON(t, w, map[string]any{"requestId": "request_1", "status": "pending", "manifestId": nil})
			}})
			sandbox := dockerVolumeSandbox(t, fake, WithRequestTimeout(time.Nanosecond))
			// When
			result, err := sandbox.Volumes().Pull(t.Context(), "volume_1", "request_1", test.opts)
			// Then
			require.NoError(t, err)
			require.Equal(t, PullVolumeResult{RequestID: "request_1", Status: PullVolumeStatusPending, RawStatus: "pending"}, result)
			require.Len(t, fake.requests(), 1)
			require.JSONEq(t, test.body, string(fake.requests()[0].Body))
			require.Equal(t, "application/json", fake.requests()[0].Header.Get("Content-Type"))
		})
	}
}

func TestSandboxVolumesPull_whenServerReturnsOutcome(t *testing.T) {
	manifest := "manifest"
	for _, test := range []struct {
		raw      string
		want     PullVolumeStatus
		manifest *string
	}{
		{"pulled", PullVolumeStatusPulled, &manifest},
		{"unchanged", PullVolumeStatusUnchanged, &manifest},
		{"unchanged", PullVolumeStatusUnchanged, nil},
		{"pending", PullVolumeStatusPending, nil},
		{"future", PullVolumeStatusUnknown, nil},
	} {
		t.Run(test.raw, func(t *testing.T) {
			// Given
			id := strings.Repeat("a", 128)
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /volumes/{volume}/pull": jsonHandler(t, map[string]any{
				"requestId": id, "status": test.raw, "manifestId": test.manifest, "future": true,
			})})
			sandbox := dockerVolumeSandbox(t, fake)
			// When
			result, err := sandbox.Volumes().Pull(t.Context(), id, id, nil)
			// Then
			require.NoError(t, err)
			require.Equal(t, PullVolumeResult{RequestID: id, Status: test.want, RawStatus: test.raw, ManifestID: test.manifest}, result)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestSandboxVolumesPull_whenIDsInvalid(t *testing.T) {
	for _, id := range []string{"", "a/b", "..", "a.b", "a%2Fb", "a?b", "a#b", "a b", "é", "a\n", strings.Repeat("a", 129)} {
		for _, requestID := range []bool{false, true} {
			t.Run(id+map[bool]string{false: "/volume", true: "/request"}[requestID], func(t *testing.T) {
				// Given
				fake := newFakeServer(t, nil)
				sandbox := dockerVolumeSandbox(t, fake)
				volume, request := id, "valid"
				if requestID {
					volume, request = "valid", id
				}
				// When
				_, err := sandbox.Volumes().Pull(t.Context(), volume, request, nil)
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				var sdk *Error
				require.ErrorAs(t, err, &sdk)
				require.Equal(t, "SDK", sdk.Source)
				require.Empty(t, fake.requests())
			})
		}
	}
}

func TestSandboxVolumesPull_whenTimeoutInvalid(t *testing.T) {
	for _, timeout := range []time.Duration{-time.Second, WaitForever, NoTimeout, 30*time.Second + time.Nanosecond} {
		t.Run(timeout.String(), func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := dockerVolumeSandbox(t, fake)
			// When
			_, err := sandbox.Volumes().Pull(t.Context(), "volume", "request", &PullVolumeOptions{Timeout: timeout})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestSandboxVolumesPull_whenCallerCancelsActiveRequest(t *testing.T) {
	// Given
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /volumes/volume/pull": func(_ http.ResponseWriter, r *http.Request) {
		cancel()
		<-r.Context().Done()
	}})
	sandbox := dockerVolumeSandbox(t, fake)
	// When
	_, err := sandbox.Volumes().Pull(ctx, "volume", "request", nil)
	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.Len(t, fake.requests(), 1)
}
