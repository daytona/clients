// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestNew_whenAPIBaseHasQueryOrFragment(t *testing.T) {
	for _, suffix := range []string{"?route=one", "#anchor", "?", "#"} {
		t.Run(suffix, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			t.Setenv("DAYTONA_V2_API_URL", fake.Server.URL+"/v2"+suffix)
			// When
			client, err := New(WithAPIKey("test-key"))
			if client != nil {
				t.Cleanup(func() { require.NoError(t, client.Close()) })
			}
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestTransport_whenAPIBaseHasEscapedPath(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /": jsonHandler(t, struct{}{}),
	})
	client := newTestClient(t, fake.Server)
	t.Setenv("DAYTONA_V2_API_URL", fake.Server.URL+"/v2/tenant%2Fone/")
	configured, err := New(WithAPIKey("test-key"), WithHTTPClient(client.http))
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, configured.Close()) })
	// When
	err = configured.api().do(t.Context(), apiRequest{Path: "/echo"})
	// Then
	require.NoError(t, err)
	require.Equal(t, "/v2/tenant%2Fone/echo", fake.requests()[0].Path)
}
