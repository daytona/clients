// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestBuildLogsDiscoveryDenialIsNotInterruption(t *testing.T) {
	for _, status := range []int{400, 401, 403, 404, 409} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			// Given a denied discovery request.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/build-logs-url": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(status)
					writeJSON(t, w, map[string]string{"code": "FUTURE_ERROR", "message": "denied", "source": "API"})
				},
			})
			client := newTestClient(t, fake.Server)
			// When following the sandbox build.
			_, err := buildLogsCollect((&Sandbox{client: client, id: "sbx"}).FollowBuildLogs(t.Context(), nil))
			// Then the core error is returned unchanged, with no fallback.
			var interrupted *BuildLogInterruptedError
			require.NotErrorAs(t, err, &interrupted)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Equal(t, status, sdkError.StatusCode)
			require.Equal(t, "FUTURE_ERROR", sdkError.Code)
			require.Equal(t, "API", sdkError.Source)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestBuildLogsTargetErrorsPreserveCoreMapping(t *testing.T) {
	tests := []struct {
		name        string
		contentType string
		code        string
		wantCode    string
	}{
		{"JSON", "Application/JSON; charset=utf-8", "REGION_MISMATCH", "REGION_MISMATCH"},
		{"plain text", "text/plain", "REGION_MISMATCH", ""},
		{"JSON lines", "application/jsonl", "REGION_MISMATCH", ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Given a target error with diagnostic metadata.
			fake := buildLogsFake(t, func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", tt.contentType)
				w.WriteHeader(http.StatusBadRequest)
				// writeJSON changes the header after it was committed, retaining tt.contentType.
				writeJSON(t, w, map[string]string{
					"code": tt.code, "message": "mismatch", "source": "DAYTONA_ROUTER", "trace_id": "trace",
				})
			})
			client := newTestClient(t, fake.Server)
			// When following from an opaque checkpoint.
			_, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image",
				&FollowBuildLogsOptions{Cursor: "opaque"}))
			// Then the interruption retains the checkpoint and mapped cause.
			var interrupted *BuildLogInterruptedError
			require.ErrorAs(t, err, &interrupted)
			require.Equal(t, "opaque", interrupted.Cursor)
			require.ErrorIs(t, err, ErrBadRequest)
			var cause *Error
			require.ErrorAs(t, interrupted.SDKError.Err, &cause)
			require.Equal(t, tt.wantCode, cause.Code)
			if tt.wantCode != "" {
				require.Equal(t, "DAYTONA_ROUTER", cause.Source)
				require.Equal(t, "trace", cause.TraceID)
			}
			require.Len(t, fake.requests(), 2)
		})
	}
}

func TestBuildLogsRedirectIsNeverFollowed(t *testing.T) {
	// Given a discovered endpoint returning a redirect to another resource.
	fake := buildLogsFake(t, func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Location", "/stolen")
		w.WriteHeader(http.StatusTemporaryRedirect)
	})
	client := newTestClient(t, fake.Server)
	// When observing the stream.
	_, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image", nil))
	// Then core redirect refusal stops observation.
	var interrupted *BuildLogInterruptedError
	require.ErrorAs(t, err, &interrupted)
	require.Equal(t, http.StatusTemporaryRedirect, interrupted.SDKError.StatusCode)
	require.Len(t, fake.requests(), 2)
}

func TestBuildLogsSnapshotMissingBuildUsesCoreError(t *testing.T) {
	// Given a sandbox with no image build.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/build-logs-url": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusNotFound)
			writeJSON(t, w, map[string]string{"code": "NOT_FOUND", "message": "no image build"})
		},
	})
	client := newTestClient(t, fake.Server)
	// When reading build logs.
	_, err := (&Sandbox{client: client, id: "sbx"}).BuildLogs(t.Context())
	// Then no entrypoint or alternate log source is attempted.
	require.ErrorIs(t, err, ErrNotFound)
	require.Len(t, fake.requests(), 1)
}

func TestBuildLogsReusesImageBuildFailedError(t *testing.T) {
	// Given the API's typed image-build failure.
	fake := buildLogsFake(t, func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		writeJSON(t, w, map[string]string{"code": "IMAGE_BUILD_FAILED", "source": SourceAPI, "message": "failed"})
	})
	client := newTestClient(t, fake.Server)
	// When following the build.
	_, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image", nil))
	// Then the shared typed error remains available in the interruption's chain.
	var interrupted *BuildLogInterruptedError
	require.ErrorAs(t, err, &interrupted)
	var failed *ImageBuildFailedError
	require.ErrorAs(t, err, &failed)
	require.ErrorIs(t, err, ErrImageBuildFailed)
	require.Len(t, fake.requests(), 2)
}
