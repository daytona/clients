// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"net/url"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestComputerUseScreenshot_whenRegionOrCompressionSelected(t *testing.T) {
	quality, scale, cursor := 0, 0.5, false
	region := &ComputerUseRegion{X: 1, Y: 2, Width: 30, Height: 40}
	cases := []struct {
		name, path string
		opts       *ComputerUseScreenshotOptions
		query      url.Values
	}{
		{"default", "/computeruse/screenshot", nil, url.Values{}},
		{
			"region", "/computeruse/screenshot/region",
			&ComputerUseScreenshotOptions{Region: region, ShowCursor: &cursor},
			url.Values{"x": {"1"}, "y": {"2"}, "width": {"30"}, "height": {"40"}, "show_cursor": {"false"}},
		},
		{
			"compressed", "/computeruse/screenshot/compressed",
			&ComputerUseScreenshotOptions{Format: ComputerUseScreenshotJPEG, Quality: &quality, Scale: &scale},
			url.Values{"format": {"jpeg"}, "quality": {"0"}, "scale": {"0.5"}},
		},
		{
			"compressed region", "/computeruse/screenshot/region/compressed",
			&ComputerUseScreenshotOptions{Region: region, Quality: &quality},
			url.Values{"x": {"1"}, "y": {"2"}, "width": {"30"}, "height": {"40"}, "quality": {"0"}},
		},
		{
			"scale alone", "/computeruse/screenshot/compressed", &ComputerUseScreenshotOptions{Scale: &scale},
			url.Values{"scale": {"0.5"}},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			sandbox, fake := computerUseTestSandbox(t, jsonHandler(t, map[string]any{
				"screenshot": "AAECA/8=", "sizeBytes": 5,
				"cursorPosition": map[string]int{"x": 12, "y": 24},
			}))
			// When
			got, err := sandbox.ComputerUse().Screen().Screenshot(t.Context(), tc.opts)
			// Then
			require.NoError(t, err)
			require.Equal(t, []byte{0, 1, 2, 3, 255}, got.Data)
			require.EqualValues(t, 5, got.SizeBytes)
			require.Equal(t, &ComputerUsePoint{X: 12, Y: 24}, got.CursorPosition)
			req := fake.requests()[1]
			require.Equal(t, http.MethodGet, req.Method)
			require.Equal(t, tc.path, req.Path)
			require.Equal(t, tc.query, req.Query)
			require.Empty(t, req.Body)
		})
	}
}

func TestComputerUseDisplays_whenInventoryRead(t *testing.T) {
	t.Run("displays", func(t *testing.T) {
		// Given
		sandbox, fake := computerUseTestSandbox(t, jsonHandler(t, map[string]any{
			"displays": []map[string]any{{"id": 0, "x": -100, "y": 0, "width": 1280, "height": 720, "isActive": true}},
		}))
		// When
		got, err := sandbox.ComputerUse().Displays().Info(t.Context())
		// Then
		require.NoError(t, err)
		require.Equal(t, []ComputerUseDisplay{{ID: 0, X: -100, Y: 0, Width: 1280, Height: 720, IsActive: true}}, got)
		require.Equal(t, "/computeruse/display/info", fake.requests()[1].Path)
	})
	t.Run("windows", func(t *testing.T) {
		// Given
		sandbox, fake := computerUseTestSandbox(t, jsonHandler(t, map[string]any{
			"windows": []map[string]any{{"id": 42, "title": "Editor", "x": 0, "y": 10, "width": 640, "height": 480, "isActive": false}},
		}))
		// When
		got, err := sandbox.ComputerUse().Displays().Windows(t.Context())
		// Then
		require.NoError(t, err)
		require.Equal(t, []ComputerUseWindow{{ID: 42, Title: "Editor", X: 0, Y: 10, Width: 640, Height: 480}}, got)
		require.Equal(t, "/computeruse/display/windows", fake.requests()[1].Path)
	})
}
