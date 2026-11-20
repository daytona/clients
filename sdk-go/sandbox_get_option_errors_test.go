// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestGet_whenOptionIsInvalid_namesGoFields(t *testing.T) {
	for _, test := range []struct {
		name string
		opts GetSandboxOptions
	}{
		{"ToolboxURL", GetSandboxOptions{ToolboxURL: " "}},
		{"AccessToken", GetSandboxOptions{AccessToken: "\n"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.Get(t.Context(), "sbx", &test.opts)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, test.name)
			require.Empty(t, fake.requests())
		})
	}
}

func TestGet_whenResponseExceedsLimit_namesGoOption(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx": jsonHandler(t, sandboxFixture("sbx")),
	})
	client := newTestClient(t, fake.Server, WithMaxResponseBytes(1))
	// When
	_, err := client.Get(t.Context(), "sbx", nil)
	// Then
	require.ErrorIs(t, err, ErrPayloadTooLarge)
	require.ErrorContains(t, err, "WithMaxResponseBytes")
}
