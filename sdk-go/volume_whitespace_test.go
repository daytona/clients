// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestVolumesCreate_whenNameIsSeparatorWhitespace(t *testing.T) {
	for _, name := range []string{"\x1c", "\x1d", "\x1e", "\x1f", " \t\x1c\x1d\x1e\x1f\n"} {
		t.Run(fmt.Sprintf("%q", name), func(t *testing.T) {
			// Given a name containing only Python-compatible whitespace.
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /volumes": jsonHandler(t, volumeFixture())})
			client := newTestClient(t, fake.Server)
			// When attempting to create a volume.
			_, err := client.Volumes().Create(t.Context(), name, nil)
			// Then the invalid name is rejected before any request.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestVolumesCreate_whenResponseIDIsSeparatorWhitespace(t *testing.T) {
	for _, id := range []string{"\x1c", "\x1d", "\x1e", "\x1f", " \t\x1c\x1d\x1e\x1f\n"} {
		t.Run(fmt.Sprintf("%q", id), func(t *testing.T) {
			// Given a successful response whose ID contains only whitespace.
			fixture := volumeFixture()
			fixture["id"] = id
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /volumes": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When creating a volume.
			_, err := client.Volumes().Create(t.Context(), "data", nil)
			// Then the malformed ID cannot become a usable volume.
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, "id")
		})
	}
}
