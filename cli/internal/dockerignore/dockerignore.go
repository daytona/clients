// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package dockerignore reads .dockerignore files and decides which paths of a
// build context are uploaded, the way `docker build` (BuildKit) does: the
// file is read like moby's ignorefile.ReadAll, every pattern is compiled like
// moby/patternmatcher, and a path is matched with the results of its parent
// directory carried down the walk (patternmatcher's
// MatchesUsingParentResults, which BuildKit's context walker uses).
package dockerignore

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"path"
	"path/filepath"
	"regexp"
	"strings"
)

// Pattern is one compiled line of a .dockerignore file.
type Pattern struct {
	raw       string // the line as given, for errors
	err       error  // why the pattern cannot match: reported when evaluated
	text      string // cleaned, slash-separated, without the leading `!`
	exclusion bool   // a `!` line: re-includes what earlier lines excluded
	kind      matchKind
	re        *regexp.Regexp
}

type matchKind int

const (
	matchExact matchKind = iota
	matchPrefix
	matchSuffix
	matchRegexp
)

// Matcher holds the patterns of one ignore file.
type Matcher struct {
	patterns   []*Pattern
	exclusions bool
	// wildcardExceptions: some `!` pattern is more than a path prefix, so
	// it may re-include something below any directory.
	wildcardExceptions bool
}

// Results are one directory's per-pattern match results, handed to the
// entries inside it. The zero value means "no parent information".
type Results struct {
	matched []bool
}

// ReadPatterns parses an ignore file as moby's ignorefile.ReadAll does: a
// UTF-8 BOM on the first line is dropped, a line starting with `#` is a
// comment, surrounding whitespace is trimmed, and each pattern (after a
// leading `!`) is cleaned and loses a leading `/`. A line longer than 64 KiB
// is an error.
func ReadPatterns(r io.Reader) ([]string, error) {
	var out []string
	scanner := bufio.NewScanner(r)
	first := true
	for scanner.Scan() {
		line := scanner.Bytes()
		if first {
			line = bytes.TrimPrefix(line, []byte("\xEF\xBB\xBF"))
			first = false
		}
		text := string(line)
		if strings.HasPrefix(text, "#") {
			continue
		}
		text = strings.TrimSpace(text)
		if text == "" {
			continue
		}
		invert := text[0] == '!'
		if invert {
			text = strings.TrimSpace(text[1:])
		}
		if text != "" {
			text = filepath.ToSlash(filepath.Clean(text))
			if len(text) > 1 && text[0] == '/' {
				text = text[1:]
			}
		}
		if invert {
			text = "!" + text
		}
		out = append(out, text)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return out, nil
}

// PatternError is a pattern that cannot be matched, reported when the walk
// first evaluates it, as `docker build` does.
type PatternError struct {
	Pattern string
	Err     error
}

func (e *PatternError) Error() string {
	return fmt.Sprintf("invalid pattern %q: %v", e.Pattern, e.Err)
}

func (e *PatternError) Unwrap() error { return e.Err }

// New compiles patterns (as ReadPatterns returns them). Like
// patternmatcher.New it refuses only an illegal `!` and a pattern that
// filepath.Match refuses; a pattern that does not compile is an error only
// when a path is matched against it (PatternError).
func New(patterns []string) (*Matcher, error) {
	m := &Matcher{}
	for _, raw := range patterns {
		text := strings.TrimSpace(raw)
		if text == "" {
			continue
		}
		// Like patternmatcher.New: the whole line is cleaned before a leading
		// `!` is read, so `!/../x` cleans to `x` (an inclusion) and `!/` to
		// the illegal `!`.
		text = path.Clean(text)
		p := &Pattern{}
		if text[0] == '!' {
			if len(text) == 1 {
				return nil, fmt.Errorf("illegal exclusion pattern %q", raw)
			}
			p.exclusion = true
			m.exclusions = true
			text = text[1:]
		}
		if _, err := filepath.Match(filepath.FromSlash(text), "."); err != nil {
			return nil, fmt.Errorf("invalid pattern %q: %w", raw, err)
		}
		p.text = text
		p.raw = raw
		if err := p.compile(); err != nil {
			p.err = &PatternError{Pattern: raw, Err: err}
		}
		m.patterns = append(m.patterns, p)
		if p.exclusion && strings.ContainsAny(withoutTrailingGlob(p.text), patternChars) {
			m.wildcardExceptions = true
		}
	}
	return m, nil
}

// patternChars are the characters that make an exception more than a path
// prefix (BuildKit's list).
const patternChars = "*[]?^\\"

// withoutTrailingGlob drops a trailing `/**` or `/*`.
func withoutTrailingGlob(text string) string {
	text = strings.TrimSuffix(text, "/**")
	return strings.TrimSuffix(text, "/*")
}

// Match decides one path given its parent directory's results (the zero
// Results for a path at the context root), as MatchesUsingParentResults
// does: a pattern that matched the parent directory matches the path too,
// and every other pattern is evaluated against the path alone, in order,
// while it can still change the outcome. It returns whether the path is
// excluded and the results to hand to the entries inside it, or the
// PatternError of a pattern evaluated here that cannot be matched.
func (m *Matcher) Match(rel string, parent Results) (bool, Results, error) {
	rel = path.Clean(rel)
	results := Results{matched: make([]bool, len(m.patterns))}
	known := len(parent.matched) == len(m.patterns) && len(m.patterns) > 0
	excluded := false
	for i, p := range m.patterns {
		match := known && parent.matched[i]
		if !match {
			// An inclusion line only matters while the path is included, an
			// exclusion (`!`) line only while it is excluded.
			if p.exclusion != excluded {
				continue
			}
			if p.err != nil {
				return false, results, p.err
			}
			match = p.match(rel)
			if !match && !known {
				// No parent information: check every parent directory.
				if parent := path.Dir(rel); parent != "." {
					dirs := strings.Split(parent, "/")
					for i := 0; !match && i < len(dirs); i++ {
						match = p.match(strings.Join(dirs[:i+1], "/"))
					}
				}
			}
		}
		results.matched[i] = match
		if match {
			excluded = !p.exclusion
		}
	}
	return excluded, results, nil
}

// Excluded reports whether the slash-separated context path rel is excluded
// when the walk reaches it: each parent directory is matched first and its
// results are carried down, as in a walk of the context.
func (m *Matcher) Excluded(rel string) (bool, error) {
	rel = path.Clean(rel)
	if rel == "." {
		return false, nil
	}
	var results Results
	excluded := false
	parts := strings.Split(rel, "/")
	for i := range parts {
		var err error
		if excluded, results, err = m.Match(strings.Join(parts[:i+1], "/"), results); err != nil {
			return false, err
		}
	}
	return excluded, nil
}

// MayReinclude reports whether an excluded directory must still be walked
// because a `!` pattern may name something inside it, as BuildKit decides:
// never without exceptions; always when an exception has a wildcard (other
// than a trailing `/*` or `/**`); otherwise when an exception is a path below
// the directory.
func (m *Matcher) MayReinclude(dir string) bool {
	if !m.exclusions {
		return false
	}
	if m.wildcardExceptions {
		return true
	}
	prefix := path.Clean(dir) + "/"
	for _, p := range m.patterns {
		if p.exclusion && strings.HasPrefix(withoutTrailingGlob(p.text)+"/", prefix) {
			return true
		}
	}
	return false
}

func (p *Pattern) match(rel string) bool {
	switch p.kind {
	case matchExact:
		return rel == p.text
	case matchPrefix:
		return strings.HasPrefix(rel, strings.TrimSuffix(p.text, "**"))
	case matchSuffix:
		suffix := p.text[2:] // as patternmatcher: the bytes after the leading `**`
		return strings.HasSuffix(rel, suffix) || (strings.HasPrefix(suffix, "/") && rel == suffix[1:])
	default:
		return p.re.MatchString(rel)
	}
}

// compile turns the pattern into a match kind: plain text matches exactly,
// `prefix/**` and `**/suffix` by prefix and suffix, anything else through a
// regular expression where `*` and `?` stay within one segment and `**`
// spans segments.
func (p *Pattern) compile() error {
	var re strings.Builder
	re.WriteString("^")
	kind := matchExact
	// patternmatcher reads the pattern with text/scanner, which skips a
	// leading byte order mark.
	runes := []rune(strings.TrimPrefix(p.text, "\uFEFF"))
	for i, token := 0, 0; i < len(runes); i, token = i+1, token+1 {
		ch := runes[i]
		switch {
		case ch == '*' && i+1 < len(runes) && runes[i+1] == '*':
			i++
			if i+1 < len(runes) && runes[i+1] == '/' {
				i++
			}
			if i+1 == len(runes) {
				if kind == matchExact {
					kind = matchPrefix
				} else {
					re.WriteString(".*")
					kind = matchRegexp
				}
			} else {
				re.WriteString("(.*/)?")
				kind = matchRegexp
			}
			if token == 0 {
				kind = matchSuffix
			}
		case ch == '*':
			re.WriteString("[^/]*")
			kind = matchRegexp
		case ch == '?':
			re.WriteString("[^/]")
			kind = matchRegexp
		case strings.ContainsRune(".+()|{}$", ch):
			re.WriteString(`\` + string(ch))
		case ch == '\\':
			if i+1 < len(runes) {
				i++
				re.WriteString(`\` + string(runes[i]))
				kind = matchRegexp
			} else {
				re.WriteString(`\\`)
			}
		case ch == '[' || ch == ']':
			re.WriteRune(ch)
			kind = matchRegexp
		default:
			re.WriteRune(ch)
		}
	}
	p.kind = kind
	if kind != matchRegexp {
		return nil
	}
	re.WriteString("$")
	compiled, err := regexp.Compile(re.String())
	if err != nil {
		return err
	}
	p.re = compiled
	return nil
}

// Filter decides which entries of a build context are uploaded: what the
// matcher does not exclude, plus the paths given to NewFilter, which are
// always kept (the Dockerfile and the ignore file). It fits
// toolbox.ArchiveFilter.
type Filter struct {
	matcher *Matcher
	always  map[string]bool
	// alwaysDirs are the parent directories of the always-kept paths.
	alwaysDirs map[string]bool
}

// NewFilter returns a filter over m that always keeps the slash-separated
// context paths in always, without matching them.
func NewFilter(m *Matcher, always ...string) *Filter {
	f := &Filter{matcher: m, always: map[string]bool{}, alwaysDirs: map[string]bool{}}
	for _, rel := range always {
		rel = path.Clean(rel)
		f.always[rel] = true
		for dir := path.Dir(rel); dir != "." && dir != "/"; dir = path.Dir(dir) {
			f.alwaysDirs[dir] = true
		}
	}
	return f
}

// Keep reports whether the entry is uploaded and, for a directory that is
// not, whether the walk still looks inside it. parent is the state Keep
// returned for the entry's directory (nil at the context root); state is
// handed to the directory's own entries.
func (f *Filter) Keep(rel string, isDir bool, parent any) (keep, descend bool, state any, err error) {
	parentResults, _ := parent.(Results)
	excluded, results, err := f.matcher.Match(rel, parentResults)
	if err != nil {
		return false, false, nil, err
	}
	if isDir {
		state = results
	}
	if !excluded || f.always[rel] {
		return true, true, state, nil
	}
	return false, isDir && (f.alwaysDirs[rel] || f.matcher.MayReinclude(rel)), state, nil
}
