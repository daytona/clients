// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package dockerignore

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestExcluded_whenSharedSDKEdgeCase(t *testing.T) {
	// Given the same expectations consumed by both SDK suites.
	data, err := os.ReadFile("../../../sdk-python/tests/fixtures/dockerignore-edge-cases.json")
	require.NoError(t, err)
	var cases []struct {
		Ignore   string   `json:"ignore"`
		Paths    []string `json:"paths"`
		Excluded []bool   `json:"excluded"`
	}
	require.NoError(t, json.Unmarshal(data, &cases))
	for _, fixture := range cases {
		t.Run(fixture.Ignore, func(t *testing.T) {
			patterns, err := ReadPatterns(strings.NewReader(fixture.Ignore))
			require.NoError(t, err)
			matcher, err := New(patterns)
			require.NoError(t, err)
			require.Len(t, fixture.Excluded, len(fixture.Paths))
			for index, path := range fixture.Paths {
				// When evaluated by Go's reference implementation.
				excluded, err := matcher.Excluded(path)
				// Then every expectation agrees with the oracle, including errors.
				require.NoError(t, err)
				require.Equal(t, fixture.Excluded[index], excluded, "%q", path)
			}
		})
	}
}
