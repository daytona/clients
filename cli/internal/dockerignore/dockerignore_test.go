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
		require.Equal(t, want, m.excludes(rel), rel)
	}
}

func TestExcluded_lastMatchingPatternWins(t *testing.T) {
	m := matcher(t, "*.txt\n!important.txt\nimportant.txt\n")
	require.True(t, m.excludes("important.txt"))
	m = matcher(t, "secrets\n!secrets/public\n")
	require.True(t, m.excludes("secrets/private.key"))
	require.False(t, m.excludes("secrets/public"))
	require.False(t, m.excludes("secrets/public/cert.pem"))
}

func TestMayReinclude_onlyForDirectoriesHoldingAnException(t *testing.T) {
	m := matcher(t, "secrets\n!secrets/public\n")
	require.True(t, m.MayReinclude("secrets"))
	require.False(t, m.MayReinclude("other"))
	require.False(t, matcher(t, "secrets\n").MayReinclude("secrets"))
	// A wildcard exception can match below any directory.
	wild := matcher(t, "vendor\n!**/LICENSE\n")
	require.True(t, wild.MayReinclude("vendor"))
	require.True(t, wild.excludes("vendor/lib/code.go"))
	require.False(t, wild.excludes("vendor/lib/LICENSE"))
}

func TestNew_rejectsInvalidPatterns(t *testing.T) {
	_, err := New([]string{"!"})
	require.Error(t, err)
	_, err = New([]string{"[a-"})
	require.Error(t, err)
}

func TestExcluded_escapedMetacharacters(t *testing.T) {
	m := matcher(t, `file\*.txt`+"\nv1.0\n")
	require.True(t, m.excludes("file*.txt"))
	require.False(t, m.excludes("fileX.txt"))
	require.True(t, m.excludes("v1.0"))
	require.False(t, m.excludes("v1x0"))
}

// A pattern counts for a path's parent directories only if it was evaluated
// at that directory (BuildKit's MatchesUsingParentResults): an exception
// that re-includes a directory does not re-include what other patterns
// exclude inside it, while an exception skipped at the parent stays skipped.
func TestExcluded_carriesParentResultsLikeBuildKit(t *testing.T) {
	// A leading `**` without `/` is a suffix match.
	m := matcher(t, "**foo\n")
	for _, rel := range []string{"foo", "x/foo", "barfoo", "x/barfoo"} {
		require.True(t, m.excludes(rel), rel)
	}
	// `^` is not escaped: the pattern can never match.
	m = matcher(t, "*.log^\n")
	require.False(t, m.excludes("app.log^"))
	require.False(t, m.excludes("app.log"))
	// `!config` re-includes the directory, not the secrets inside it.
	secrets := matcher(t, "**/*.pem\n**/.env\n!config\n")
	require.False(t, secrets.excludes("config"))
	require.False(t, secrets.excludes("config/app.yaml"))
	require.True(t, secrets.excludes("config/server.pem"))
	require.True(t, secrets.excludes("config/.env"))
	require.True(t, matcher(t, "**/*.key\n!certs\n").excludes("certs/server.key"))
	// The second `docs` is skipped at `docs` (already excluded there), so it
	// does not count for what is inside.
	require.False(t, matcher(t, "docs\n!docs/README.md\ndocs\n").excludes("docs/README.md"))
	require.True(t, matcher(t, "*/*.log\n!build\n").excludes("build/x.log"))
	// A pattern that matched a parent directory applies to its contents.
	require.True(t, matcher(t, "a/b\n!a\n").excludes("a/b/c"))
}

func TestExcluded_wildcards(t *testing.T) {
	// `**` in the middle spans zero or more segments.
	m := matcher(t, "a/**/c\n")
	for rel, want := range map[string]bool{"a/c": true, "a/b/c": true, "a/b/d/c": true, "a/b/x": false} {
		require.Equal(t, want, m.excludes(rel), rel)
	}
	// `?` is one character other than `/`.
	m = matcher(t, "a?c\n")
	require.True(t, m.excludes("abc"))
	require.False(t, m.excludes("a/c"))
}

func TestReadPatterns_likeDockersIgnorefile(t *testing.T) {
	// Only a line that starts with `#` is a comment: " #x" is the pattern "#x".
	patterns, err := ReadPatterns(strings.NewReader(" #x\n#y\n"))
	require.NoError(t, err)
	require.Equal(t, []string{"#x"}, patterns)
	// A byte order mark is dropped on the first line only.
	patterns, err = ReadPatterns(strings.NewReader("\n\xEF\xBB\xBF*.log\n"))
	require.NoError(t, err)
	require.Equal(t, []string{"\uFEFF*.log"}, patterns)
	// The matcher then skips it, as Docker's does: the pattern is `*.log`.
	require.True(t, matcher(t, "\n\xEF\xBB\xBF*.log\n").excludes("a.log"))
	// A line longer than 64 KiB is an error.
	_, err = ReadPatterns(strings.NewReader(strings.Repeat("a", 70*1024) + "\n"))
	require.Error(t, err)
}

func TestNew_cleansTheWholeLineBeforeReadingTheExclamationMark(t *testing.T) {
	// `!../../*` cleans to `*`, an inclusion.
	require.True(t, matcher(t, "!../../*\n").excludes("anything"))
	// `!/` cleans to the illegal `!`.
	patterns, err := ReadPatterns(strings.NewReader("!/\n"))
	require.NoError(t, err)
	_, err = New(patterns)
	require.Error(t, err)
}

func TestMayReinclude_followsBuildKit(t *testing.T) {
	// A trailing `/**` keeps an exception a path prefix.
	m := matcher(t, "build\n!build/keep/**\n")
	require.True(t, m.MayReinclude("build"))
	require.False(t, m.MayReinclude("other"))
	// Any other wildcard in an exception may match below any directory.
	require.True(t, matcher(t, "build\n!*.txt\n").MayReinclude("other"))
}

// A pattern that does not compile is an error only once a path is matched
// against it, as in Docker's matcher; one never evaluated is harmless.
func TestPatternThatDoesNotCompileFailsWhenEvaluated(t *testing.T) {
	m, err := New([]string{`foo\q`})
	require.NoError(t, err)
	_, err = m.Excluded("x")
	var pe *PatternError
	require.ErrorAs(t, err, &pe)
	require.Equal(t, `foo\q`, pe.Pattern)
	// The exception is never evaluated: nothing is excluded when it comes.
	m, err = New([]string{"a", `!foo\q`})
	require.NoError(t, err)
	excluded, err := m.Excluded("b")
	require.NoError(t, err)
	require.False(t, excluded)
	// filepath.Match's syntax check is still up front.
	_, err = New([]string{"[a-"})
	require.Error(t, err)
}

// excludes is Excluded for patterns that compile.
func (m *Matcher) excludes(rel string) bool {
	excluded, err := m.Excluded(rel)
	if err != nil {
		panic(err)
	}
	return excluded
}
