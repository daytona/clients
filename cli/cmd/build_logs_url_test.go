// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestValidatedBuildLogBaseURL_acceptsMatchingResource(t *testing.T) {
	for _, tc := range []struct {
		name, discovery, target string
	}{
		{"sandbox", "/sandboxes/sbx-a/build-logs-url", "https://logs.example/build-logs/sandboxes/sbx-a"},
		{"image", "/images/11111111-1111-1111-1111-111111111111/build-logs-url", "https://logs.example/build-logs/images/11111111-1111-1111-1111-111111111111"},
		{"other collection", "/artifacts.v2/item-a/build-logs-url", "https://logs.example/prefix/build-logs/artifacts.v2/item-a"},
		{"UUID spelling", "/images/11111111111111111111111111111111/build-logs-url", "https://logs.example/build-logs/images/11111111-1111-1111-1111-111111111111"},
		{"sandbox identity", "/sandboxes/37EWfWxhwVEGTT5BcjfE2L-region-a/build-logs-url", "https://logs.example/build-logs/sandboxes/11111111-1111-1111-1111-111111111111"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given a discovery path and a URL for the same resource.
			// When the URL is validated.
			got, err := validatedBuildLogBaseURL(tc.discovery, tc.target)
			// Then the complete URL, including any base path, is preserved.
			require.NoError(t, err)
			require.Equal(t, tc.target, got)
		})
	}
}

func TestValidatedBuildLogBaseURL_rejectsUnsafeOrMismatchedURL(t *testing.T) {
	for _, target := range []string{
		"/build-logs/sandboxes/sbx-a",
		"ftp://logs.example/build-logs/sandboxes/sbx-a",
		"https://user:password@logs.example/build-logs/sandboxes/sbx-a",
		"https://logs.example/build-logs/sandboxes/sbx-a?token=secret",
		"https://logs.example/build-logs/sandboxes/sbx-a#fragment",
		"https://logs.example/build-logs/images/sbx-a",
		"https://logs.example/build-logs/sandboxes/sbx-b",
		"https://logs.example/build-logs//sbx-a",
		"https://logs.example/build-logs/../sbx-a",
		"https://logs.example/build-logs/sandboxes/sbx-a/extra",
		"https://logs.example/build-logs/sandboxes/sbx-a%2Fextra",
		"https://logs.example/build-logs/sandboxes/sbx-a%5Cextra",
		"https://logs.example/build-logs/sandboxes/sbx-a%00",
		"https://logs.example/build-logs/sandboxes/sbx-a%20",
		"https://logs.example/build-logs/sandboxes/37EWfWxhwVEGTT5BcjfE2L-region-b",
	} {
		t.Run(target, func(t *testing.T) {
			// Given a sandbox discovery path and an untrusted URL.
			// When the URL is validated.
			_, err := validatedBuildLogBaseURL("/sandboxes/sbx-a/build-logs-url", target)
			// Then it cannot be used to fetch build logs.
			require.Error(t, err)
		})
	}
}

func TestValidatedBuildLogBaseURL_rejectsDifferentRegion(t *testing.T) {
	// Given the same sandbox identity with different region suffixes.
	discovery := "/sandboxes/37EWfWxhwVEGTT5BcjfE2L-region-a/build-logs-url"
	target := "https://logs.example/build-logs/sandboxes/37EWfWxhwVEGTT5BcjfE2L-region-b"
	// When the returned URL is validated.
	_, err := validatedBuildLogBaseURL(discovery, target)
	// Then logs cannot be fetched from the different region.
	require.Error(t, err)
}

func TestBuildLogDiscoveryTarget_rejectsInvalidSegments(t *testing.T) {
	for _, path := range []string{
		"/sandboxes/sbx-a/logs", "/sandboxes/sbx-a/extra/build-logs-url",
		"/../sbx-a/build-logs-url", "/sand%2Fboxes/sbx-a/build-logs-url",
		"/sandboxes/../build-logs-url", "/sandboxes/sbx%2Fa/build-logs-url",
		"/sandboxes/%zz/build-logs-url",
	} {
		t.Run(path, func(t *testing.T) {
			// Given an invalid discovery path.
			// When its resource is parsed.
			_, _, ok := buildLogDiscoveryTarget(path)
			// Then no resource is accepted.
			require.False(t, ok)
		})
	}
}
