// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestImagePreservesWindowsClass_whenReturnedByAPI(t *testing.T) {
	// Given a supported Windows image returned by the API.
	fixture := imageFixture("img", "ready")
	fixture["class"] = "windows"
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /images/img": jsonHandler(t, fixture),
	})
	client := newTestClient(t, fake.Server)
	// When reading the image.
	image, err := client.Images().Get(t.Context(), "img", nil)
	// Then its normalized class identifies Windows, not an unknown future class.
	require.NoError(t, err)
	require.Equal(t, ImageClass("windows"), image.Class)
	require.Equal(t, "windows", image.RawClass)
}
