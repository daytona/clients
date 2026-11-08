// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package dockerignore

import (
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func matcher(t *testing.T, file string) *Matcher {
	t.Helper()
	patterns, err := ReadPatterns(strings.NewReader(file))
	require.NoError(t, err)
	m, err := New(patterns)
	require.NoError(t, err)
	return m
}

func TestReadPatterns_commentsWhitespaceLeadingSlashAndBOM(t *testing.T) {
	patterns, err := ReadPatterns(strings.NewReader("\xEF\xBB\xBF# comment\n\n  /secret.txt  \n! /keep/./me\n./a//b/\n"))
	require.NoError(t, err)
	require.Equal(t, []string{"secret.txt", "!keep/me", "a/b"}, patterns)
}

func TestExcluded_dockerSemantics(t *testing.T) {
	m := matcher(t, strings.Join([]string{
		".env",
		".git",
		"*.log",
		"build/**",
		"!build/keep.txt",
		"**/node_modules",
		"docs/*.md",
		"!docs/README.md",
		"tmp?",
		"[ab].bin",
	}, "\n"))
	cases := map[string]bool{
		".env":                      true,
		"sub/.env":                  false, // patterns are anchored at the context root
		".git":                      true,
		".git/config":               true, // a matched directory excludes everything below it
		"app.log":                   true,
		"logs/app.log":              false, // `*` stays within one segment
		"build/out/a.o":             true,
		"build/keep.txt":            false, // a later `!` re-includes
		"node_modules/x/index.js":   true,
		"web/node_modules/y/a.js":   true, // `**` spans any number of segments
		"docs/guide.md":             true,
		"docs/README.md":            false,
		"docs/sub/guide.md":         false,
		"tmp1":                      true,
		"tmp12":                     false,
		"a.bin":                     true,
		"c.bin":                     false,
		"Dockerfile":                false,
		"src/main.go":               false,
		"buildx/file":               false,
		"nested/build/out/a.o":      false,
		"web/node_modules":          true,
		"web/node_modules_not/file": false,
	}
	for rel, want := range cases {
		require.Equal(t, want, m.Excluded(rel), rel)
	}
}

func TestExcluded_lastMatchingPatternWins(t *testing.T) {
	m := matcher(t, "*.txt\n!important.txt\nimportant.txt\n")
	require.True(t, m.Excluded("important.txt"))
	m = matcher(t, "secrets\n!secrets/public\n")
	require.True(t, m.Excluded("secrets/private.key"))
	require.False(t, m.Excluded("secrets/public"))
	require.False(t, m.Excluded("secrets/public/cert.pem"))
}

func TestMayReinclude_onlyForDirectoriesHoldingAnException(t *testing.T) {
	m := matcher(t, "secrets\n!secrets/public\n")
	require.True(t, m.MayReinclude("secrets"))
	require.False(t, m.MayReinclude("other"))
	require.False(t, matcher(t, "secrets\n").MayReinclude("secrets"))
}

func TestNew_rejectsInvalidPatterns(t *testing.T) {
	_, err := New([]string{"!"})
	require.Error(t, err)
	_, err = New([]string{"[a-"})
	require.Error(t, err)
}

func TestExcluded_escapedMetacharacters(t *testing.T) {
	m := matcher(t, `file\*.txt`+"\nv1.0\n")
	require.True(t, m.Excluded("file*.txt"))
	require.False(t, m.Excluded("fileX.txt"))
	require.True(t, m.Excluded("v1.0"))
	require.False(t, m.Excluded("v1x0"))
}
