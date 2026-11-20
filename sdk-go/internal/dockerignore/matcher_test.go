// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package dockerignore

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestMatcherSharedGoSemantics(t *testing.T) {
	// Given shared expectations generated with Go 1.26, read without copying.
	data, err := os.ReadFile("../../../sdk-python/tests/fixtures/dockerignore-edge-cases.json")
	if errors.Is(err, os.ErrNotExist) {
		t.Skip("shared Python fixtures are not included in the Go module")
	}
	require.NoError(t, err)
	var cases []struct {
		Ignore   string
		Paths    []string
		Excluded []bool
	}
	require.NoError(t, json.Unmarshal(data, &cases))
	for _, tc := range cases {
		t.Run(tc.Ignore, func(t *testing.T) {
			patterns, parseErr := ReadPatterns(tc.Ignore)
			require.NoError(t, parseErr)
			matcher, compileErr := New(patterns)
			require.NoError(t, compileErr)
			// When each path is matched, then it agrees with the shared oracle.
			for i, name := range tc.Paths {
				excluded, matchErr := matcher.Excluded(name)
				require.NoError(t, matchErr, name)
				require.Equal(t, tc.Excluded[i], excluded, name)
			}
		})
	}
}

func TestMatcherSharedRefusals(t *testing.T) {
	// Given syntax failures shared by the SDKs.
	data, err := os.ReadFile("../../../sdk-python/tests/fixtures/dockerignore-refused.json")
	if errors.Is(err, os.ErrNotExist) {
		t.Skip("shared Python fixtures are not included in the Go module")
	}
	require.NoError(t, err)
	var cases []struct{ Ignore, Error string }
	require.NoError(t, json.Unmarshal(data, &cases))
	for _, tc := range cases {
		t.Run(tc.Ignore, func(t *testing.T) {
			patterns, parseErr := ReadPatterns(tc.Ignore)
			require.NoError(t, parseErr)
			// When constructed, then malformed glob syntax fails immediately.
			_, compileErr := New(patterns)
			require.EqualError(t, compileErr, tc.Error)
			var failure *Error
			require.ErrorAs(t, compileErr, &failure)
		})
	}
}

func TestMatcherDeferredRegexpFailure(t *testing.T) {
	for _, tc := range []struct {
		path string
		fail bool
	}{{"b", false}, {"a", true}} {
		t.Run(tc.path, func(t *testing.T) {
			// Given a malformed negation evaluated only for excluded paths.
			matcher, err := New([]string{"a", `!foo\q`})
			require.NoError(t, err)
			// When matching, then only the evaluated invalid expression fails.
			excluded, err := matcher.Excluded(tc.path)
			if tc.fail {
				var failure *Error
				require.ErrorAs(t, err, &failure)
			} else {
				require.NoError(t, err)
				require.False(t, excluded)
			}
		})
	}
}

func TestReadPatternsScannerBoundary(t *testing.T) {
	for _, size := range []int{65535, 65536} {
		// Given an ASCII line at the scanner boundary.
		line := strings.Repeat("a", size)
		// When parsed, then only the below-limit line succeeds.
		patterns, err := ReadPatterns(line)
		if size == 65536 {
			var failure *Error
			require.ErrorAs(t, err, &failure)
		} else {
			require.NoError(t, err)
			require.Equal(t, []string{line}, patterns)
		}
	}
}

func TestMatcherGo126ClassEndpoints(t *testing.T) {
	for _, expression := range []string{`x[ -\pL\pL]*`, `[z\101-\pL]x`} {
		t.Run(expression, func(t *testing.T) {
			// Given a shorthand that cannot be a range endpoint in Go 1.26.
			matcher, err := New([]string{expression})
			require.NoError(t, err)
			// When evaluated, then native RE2 parsing rejects the expression.
			_, err = matcher.Excluded("x")
			var failure *Error
			require.ErrorAs(t, err, &failure)
		})
	}
}
