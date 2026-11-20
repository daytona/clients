// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestNew_whenSeveralCredentialsEmpty_reportsFirstOption(t *testing.T) {
	// Given: every explicit credential is invalid; option application order differs.
	options := []Option{WithProjectID(""), WithJWTToken(""), WithAPIKey("")}
	for range 100 {
		// When
		client, err := New(options...)
		// Then
		require.Nil(t, client)
		require.ErrorContains(t, err, "WithAPIKey")
	}
}

func TestNew_whenConfigurationInvalid_namesGoOption(t *testing.T) {
	for _, tc := range []struct {
		name    string
		options []Option
		want    []string
	}{
		{"missing", nil, []string{"WithAPIKey", "WithJWTToken"}},
		{"collision", []Option{WithAPIKey("key"), WithJWTToken("jwt")}, []string{"WithAPIKey", "WithJWTToken"}},
		{"project", []Option{WithJWTToken("jwt")}, []string{"WithProjectID"}},
		{"response limit", []Option{WithMaxResponseBytes(-2)}, []string{"WithMaxResponseBytes"}},
		{"HTTP client", []Option{WithHTTPClient(nil)}, []string{"WithHTTPClient"}},
		{"logger", []Option{WithLogger(nil)}, []string{"WithLogger"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			t.Setenv("DAYTONA_API_KEY", "")
			t.Setenv("DAYTONA_JWT_TOKEN", "")
			t.Setenv("DAYTONA_PROJECT_ID", "")
			// When
			client, err := New(tc.options...)
			// Then
			require.Nil(t, client)
			for _, option := range tc.want {
				require.ErrorContains(t, err, option)
			}
		})
	}
}
