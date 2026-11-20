// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestImagesListPassesThroughPlatformFilter(t *testing.T) {
	for _, platform := range []ImagePlatformFilter{"", ImagePlatformOnly, ImagePlatformExclude, ImagePlatformAny} {
		t.Run(string(platform), func(t *testing.T) {
			// Given a supported platform filter and an empty image page.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /images/search": jsonHandler(t, map[string]any{"images": []any{}}),
			})
			client := newTestClient(t, fake.Server)
			// When reading a page.
			_, err := client.Images().List(t.Context(), &ListImagesOptions{Platform: platform})
			// Then supported values are passed intact and the empty default is omitted.
			require.NoError(t, err)
			requests := fake.requests()
			require.Len(t, requests, 1)
			require.Equal(t, string(platform), requests[0].Query.Get("platform"))
			require.Equal(t, platform != "", requests[0].Query.Has("platform"))
		})
	}
}

func TestImagesListRejectsUnknownPlatformFilter(t *testing.T) {
	for _, platform := range []ImagePlatformFilter{"future", "未来/+", "TRUE", " true "} {
		t.Run(string(platform), func(t *testing.T) {
			// Given a platform filter outside the SDK input vocabulary.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When reading a page.
			_, err := client.Images().List(t.Context(), &ListImagesOptions{Platform: platform})
			// Then the caller gets a local validation error without a request.
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, `Platform must be "true", "false" or "any"`)
			require.Empty(t, fake.requests())
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
