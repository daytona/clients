// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestImageGetRecognizesPlatformOrigins(t *testing.T) {
	for _, origin := range []string{"windows", "macos"} {
		t.Run(origin, func(t *testing.T) {
			// Given a platform image with an origin declared by the contract.
			wire := imageFixture("img", "ready")
			wire["origin"] = origin
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /images/img": jsonHandler(t, wire)})
			client := newTestClient(t, fake.Server)
			// When reading the image.
			image, err := client.Images().Get(t.Context(), "img", nil)
			// Then the known origin is preserved in both public fields.
			require.NoError(t, err)
			require.Equal(t, ImageOrigin(origin), image.Origin)
			require.Equal(t, origin, image.RawOrigin)
		})
	}
}

