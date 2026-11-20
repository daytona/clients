// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestComputerUseStatus_whenCauseNullOrUnknownPair(t *testing.T) {
	for _, body := range []string{
		`{"status":"active","desktop":"ready","cause":null}`,
		`{"status":"inactive","desktop":"not_started","cause":null}`,
		`{"status":"inactive","desktop":"starting","cause":null}`,
		`{"status":"active","desktop":"failed","cause":{"code":"future","message":"failure"}}`,
	} {
		t.Run(body, func(t *testing.T) {
			// Given
			sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, JSONValue(body)))
			// When
			got, err := sandbox.ComputerUse().Status(t.Context())
			// Then
			require.NoError(t, err)
			if got.RawDesktop == "failed" {
				require.Equal(t, ComputerUseDesktopUnknown, got.Desktop)
				require.Equal(t, ComputerUseActivityUnknown, got.Status)
				require.Equal(t, "future", got.Cause.RawCode)
			} else {
				require.Nil(t, got.Cause)
			}
		})
	}
}

func TestComputerUseRecording_whenOptionalFieldsAbsent(t *testing.T) {
	// Given
	sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, JSONValue(`{
		"id":"r","fileName":"r.mp4","filePath":"/r.mp4","status":"recording",
		"startTime":"2026-11-20T00:00:00Z","endTime":null,"durationSeconds":null,"sizeBytes":null
	}`)))
	// When
	got, err := sandbox.ComputerUse().Recording().Get(t.Context(), "r")
	// Then
	require.NoError(t, err)
	require.True(t, got.EndTime.IsZero())
	require.Nil(t, got.Duration)
	require.Nil(t, got.SizeBytes)
}

func TestComputerUseProcess_whenRequiredInventoryFieldMissing(t *testing.T) {
	var fixture map[string]JSONValue
	require.NoError(t, json.Unmarshal([]byte(desktopProcessFixture), &fixture))
	for _, name := range []string{
		"Running", "Priority", "AutoRestart", "Failed",
		"RestartAttempts", "RestartBackoffMs", "Status",
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			changed := make(map[string]JSONValue, len(fixture))
			for key, value := range fixture {
				if key != name {
					changed[key] = value
				}
			}
			sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, map[string]map[string]JSONValue{"wm": changed}))
			// When
			_, err := sandbox.ComputerUse().Addons().Status(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestComputerUseRecordingDownload_whenBodyReadCanceled(t *testing.T) {
	// Given
	entered := make(chan struct{})
	sandbox, _ := computerUseTestSandbox(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		w.WriteHeader(http.StatusOK)
		if err := http.NewResponseController(w).Flush(); err != nil {
			t.Errorf("flush recording headers: %v", err)
		}
		close(entered)
		<-r.Context().Done()
	})
	ctx, cancel := context.WithCancel(t.Context())
	done := make(chan error, 1)
	// When
	go func() {
		_, err := sandbox.ComputerUse().Recording().Download(ctx, "r", nil)
		done <- err
	}()
	<-entered
	cancel()
	// Then
	require.ErrorIs(t, <-done, context.Canceled)
}
