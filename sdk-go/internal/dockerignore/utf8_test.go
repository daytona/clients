// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package dockerignore

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReadPatternsReplacesEachMalformedUTF8Sequence(t *testing.T) {
	for _, tt := range []struct {
		name string
		text string
		want string
	}{
		{"invalid bytes", "a\xff\xfeb", "a\ufffd\ufffdb"},
		{"truncated sequence", "a\xe2\x82", "a\ufffd"},
		{"interrupted sequence", "a\xf0\x90\x80b", "a\ufffdb"},
		{"surrogate", "a\xed\xa0\x80b", "a\ufffd\ufffd\ufffdb"},
		{"overlong", "a\xe0\x80\x80b", "a\ufffd\ufffd\ufffdb"},
		{"out of range", "a\xf4\x90\x80\x80b", "a\ufffd\ufffd\ufffd\ufffdb"},
		{"valid replacement", "a\ufffdb", "a\ufffdb"},
		{"valid unicode", "a😀éb", "a😀éb"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given an ignore line decoded with replacement, as in Python/TS.
			// When reading its pattern.
			patterns, err := ReadPatterns(tt.text)
			// Then each maximal malformed subpart becomes one replacement.
			require.NoError(t, err)
			require.Equal(t, []string{tt.want}, patterns)
		})
	}
}
