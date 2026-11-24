// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package input

import (
	"strings"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
)

func TestDecode_preservesDesktopSettings(t *testing.T) {
	// Given
	body, err := loadBody(t, `{"desktop":{"environment":"gnome","resolution":{"width":1920,"height":1080}}}`)
	if err != nil {
		t.Fatal(err)
	}

	// When
	req, err := Decode[gen.CreateSandboxRequest](body)

	// Then
	if err != nil {
		t.Fatal(err)
	}
	if req.Desktop == nil || req.Desktop.Environment == nil || req.Desktop.Resolution == nil {
		t.Fatalf("desktop settings missing: %+v", req.Desktop)
	}
	environment, err := req.Desktop.Environment.AsDesktopEnvironment()
	if err != nil || environment != "gnome" {
		t.Fatalf("environment = %q, error = %v", environment, err)
	}
	resolution, err := req.Desktop.Resolution.AsDesktopResolution()
	if err != nil || resolution.Width != 1920 || resolution.Height != 1080 {
		t.Fatalf("resolution = %+v, error = %v", resolution, err)
	}
}

func TestDecode_rejectsUnknownDesktopFields(t *testing.T) {
	for _, tc := range []struct {
		name string
		body string
		key  string
	}{
		{"desktop", `{"desktop":{"unknown_desktop":true}}`, "unknown_desktop"},
		{"resolution", `{"desktop":{"resolution":{"width":1920,"height":1080,"unknown_resolution":true}}}`, "unknown_resolution"},
		{"resolution case", `{"desktop":{"resolution":{"Width":1920,"height":1080}}}`, "Width"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			body, err := loadBody(t, tc.body)
			if err != nil {
				t.Fatal(err)
			}

			// When
			_, err = Decode[gen.CreateSandboxRequest](body)

			// Then
			if cerr.ExitCode(err) != cerr.ExitUsage || !strings.Contains(err.Error(), tc.key) {
				t.Fatalf("expected unknown field %q usage error, got %v", tc.key, err)
			}
		})
	}
}
