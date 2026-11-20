// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFileInfo_whenMultipleFieldsMalformedReportsFirstField(t *testing.T) {
	// Given two malformed fields in the same file response.
	fixture := fileInfoFixture()
	fixture["name"], fixture["path"] = false, false
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /files/info": jsonHandler(t, fixture),
	})
	fs := newFileTestService(t, fake.Server)
	for range 100 {
		// When reading the same malformed metadata repeatedly.
		_, err := fs.Stat(t.Context(), "/file")
		// Then the first checked field is stable, independent of map iteration.
		require.ErrorIs(t, err, ErrBadRequest)
		require.ErrorContains(t, err, "name")
		require.NotContains(t, err.Error(), "path")
	}
}
