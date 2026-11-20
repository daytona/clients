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

const desktopRecordingFixture = `{
	"id":"rec-1","fileName":"capture.mp4","filePath":"/tmp/capture.mp4",
	"startTime":"2026-11-20T09:00:00Z","endTime":"2026-11-20T09:00:02Z",
	"durationSeconds":1.25,"sizeBytes":512,"status":"future"
}`

func TestComputerUseRecordings_whenOperationSent(t *testing.T) {
	runDesktopWireCases(t, []desktopWireCase{
		{
			"start default", "POST", "/computeruse/recordings/start", `{}`, desktopRecordingFixture,
			func(ctx context.Context, c *ComputerUse) error {
				got, err := c.Recording().Start(ctx, nil)
				if err == nil {
					require.Equal(t, "rec-1", got.ID)
					require.Equal(t, "capture.mp4", got.FileName)
					require.Equal(t, "/tmp/capture.mp4", got.FilePath)
					require.Equal(t, time.Date(2026, 11, 20, 9, 0, 0, 0, time.UTC), got.StartTime)
					require.Equal(t, 2*time.Second, got.EndTime.Sub(got.StartTime))
					require.Equal(t, 1250*time.Millisecond, *got.Duration)
					require.EqualValues(t, 512, *got.SizeBytes)
					require.Equal(t, "future", got.Status)
				}
				return err
			},
		},
		{
			"start label", "POST", "/computeruse/recordings/start", `{"label":""}`, desktopRecordingFixture,
			func(ctx context.Context, c *ComputerUse) error {
				label := ""
				_, err := c.Recording().Start(ctx, &ComputerUseRecordingStartOptions{Label: &label})
				return err
			},
		},
		{
			"stop", "POST", "/computeruse/recordings/stop", `{"id":"rec-1"}`, desktopRecordingFixture,
			func(ctx context.Context, c *ComputerUse) error {
				_, err := c.Recording().Stop(ctx, "rec-1")
				return err
			},
		},
		{
			"get escaped", "GET", "/computeruse/recordings/rec%2F1", "", desktopRecordingFixture,
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Recording().Get(ctx, "rec/1"); return err },
		},
		{
			"list", "GET", "/computeruse/recordings", "", `{"recordings":[` + desktopRecordingFixture + `]}`,
			func(ctx context.Context, c *ComputerUse) error {
				got, err := c.Recording().List(ctx)
				if err == nil {
					require.Len(t, got, 1)
					require.Equal(t, "rec-1", got[0].ID)
				}
				return err
			},
		},
		{
			"delete", "DELETE", "/computeruse/recordings/rec-1", "", `{}`,
			func(ctx context.Context, c *ComputerUse) error { return c.Recording().Delete(ctx, "rec-1") },
		},
	})
}

func TestComputerUseRecordingDownload_whenCapSelected(t *testing.T) {
	for _, tc := range []struct {
		name  string
		opts  *ComputerUseRecordingDownloadOptions
		large bool
	}{
		{"client default", nil, true},
		{"per call", &ComputerUseRecordingDownloadOptions{MaxResponseBytes: 8}, false},
		{"unlimited", &ComputerUseRecordingDownloadOptions{MaxResponseBytes: NoLimit}, false},
		{"zero default", &ComputerUseRecordingDownloadOptions{}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			sandbox, fake := computerUseTestSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "video/mp4")
				_, err := w.Write([]byte{0, 1, 2, 3})
				if err != nil {
					t.Errorf("write recording response: %v", err)
				}
			})
			sandbox.client.config.maxResponseBytes = 3
			// When
			got, err := sandbox.ComputerUse().Recording().Download(t.Context(), "rec/1", tc.opts)
			// Then
			if tc.large {
				require.ErrorIs(t, err, ErrPayloadTooLarge)
			} else {
				require.NoError(t, err)
				require.Equal(t, []byte{0, 1, 2, 3}, got)
			}
			req := fake.requests()[1]
			require.Equal(t, "GET", req.Method)
			require.Equal(t, "/computeruse/recordings/rec%2F1/download", req.Path)
		})
	}
}
