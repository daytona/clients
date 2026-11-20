// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestGet_whenCallerIDContainsReplacementRune_sendsExactIdentifier(t *testing.T) {
	// Given: response IDs remain stricter because JSON decoding replaces lone surrogates.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/{id}": jsonHandler(t, sandboxFixture("sbx")),
	})
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Get(t.Context(), "name-\ufffd", nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, "/sandboxes/name-%EF%BF%BD", fake.requests()[0].Path)
}

func TestGet_whenResponseIDUnusable_rejectsHandle(t *testing.T) {
	for _, id := range []string{".", "..", "name-\ufffd"} {
		t.Run(id, func(t *testing.T) {
			// Given
			detail := sandboxFixture(id)
			detail["toolbox_proxy_url"] = ""
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx": jsonHandler(t, detail),
			})
			client := newTestClient(t, fake.Server)
			// When
			sandbox, err := client.Get(t.Context(), "sbx", nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Nil(t, sandbox)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestFork_whenResponseIDContainsLoneSurrogate_rejectsHandle(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/source/fork": jsonHandler(t, JSONValue(`{"id":"sbx-\ud800","status":"pending"}`)),
	})
	source := newForkTestSource(t, fake)
	// When
	sandbox, err := source.Fork(t.Context(), &ForkOptions{Wait: NoWait})
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.Nil(t, sandbox)
	require.Len(t, fake.requests(), 1)
}
