// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package dockerignore

import (
	"strings"
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

func TestReadPatternsDecodedLineLimit(t *testing.T) {
	// Given lines whose replacement text crosses the 64 KiB byte boundary.
	invalid := strings.Repeat("\xff", 21845)
	decoded := strings.Repeat("\ufffd", 21845)
	for _, tc := range []struct {
		name string
		text string
		want []string
	}{
		{"below limit", invalid, []string{decoded}},
		{"at limit", invalid + "a", nil},
		{"above limit", strings.Repeat("\xff", 30000), nil},
		{"comment at limit", "#" + invalid, nil},
		{"BOM at limit", "\ufeff" + strings.Repeat("\xff", 21844) + "a", nil},
		{"CRLF at limit", invalid + "\r\n", nil},
		{"final CR at limit", invalid + "\r", nil},
		{"separate lines below limit", invalid + "\n" + invalid, []string{decoded, decoded}},
		{"incomplete sequences below limit", strings.Repeat("\xe2\x82", 21845), []string{decoded}},
		{"incomplete sequences at limit", strings.Repeat("\xe2\x82", 21845) + "a", nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// When reading the ignore file.
			patterns, err := ReadPatterns(tc.text)
			// Then the decoded bytes are bounded before normalization.
			if tc.want == nil {
				var failure *Error
				require.ErrorAs(t, err, &failure)
				require.Nil(t, patterns)
				return
			}
			require.NoError(t, err)
			require.Equal(t, tc.want, patterns)
		})
	}
}
