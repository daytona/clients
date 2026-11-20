// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestImagesListPassesThroughPlatformFilter(t *testing.T) {
	for _, platform := range []ImagePlatformFilter{"", ImagePlatformOnly, ImagePlatformExclude, ImagePlatformAny, "future", "未来/+"} {
		t.Run(string(platform), func(t *testing.T) {
			// Given a current or future platform filter and an empty image page.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /images/search": jsonHandler(t, map[string]any{"images": []any{}}),
			})
			client := newTestClient(t, fake.Server)
			// When reading a page.
			_, err := client.Images().List(t.Context(), &ListImagesOptions{Platform: platform})
			// Then future values are passed intact and the empty default is omitted.
			require.NoError(t, err)
			requests := fake.requests()
			require.Len(t, requests, 1)
			require.Equal(t, string(platform), requests[0].Query.Get("platform"))
			require.Equal(t, platform != "", requests[0].Query.Has("platform"))
		})
	}
}

func TestImagesListRejectsInvalidUTF8PlatformFilter(t *testing.T) {
	// Given a platform filter that is not valid UTF-8.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	// When reading a page.
	_, err := client.Images().List(t.Context(), &ListImagesOptions{Platform: "\xff"})
	// Then invalid encoding is rejected before any request.
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}
