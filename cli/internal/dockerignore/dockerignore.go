// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package dockerignore reads .dockerignore files and matches build-context
// paths against them with Docker's semantics: patterns are evaluated in
// order, a later pattern overrides an earlier one, `!pattern` re-includes,
// a pattern that matches a directory excludes everything below it, `*` and
// `?` stay within one path segment, and `**` spans any number of segments.
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
}

// ReadPatterns parses an ignore file: one pattern per line, `#` starts a
// comment line, surrounding whitespace is ignored, a leading `/` is dropped
// (every pattern is relative to the context root), and paths are cleaned.
func ReadPatterns(r io.Reader) ([]string, error) {
	var out []string
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
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

// New compiles patterns (as ReadPatterns returns them).
func New(patterns []string) (*Matcher, error) {
	m := &Matcher{}
	for _, raw := range patterns {
		text := strings.TrimSpace(raw)
		if text == "" {
			continue
		}
		p := &Pattern{}
		if text[0] == '!' {
			if len(text) == 1 {
				return nil, fmt.Errorf("illegal exclusion pattern %q", raw)
			}
			p.exclusion = true
			m.exclusions = true
			text = text[1:]
		}
		text = path.Clean(text)
		if _, err := path.Match(text, "."); err != nil {
			return nil, fmt.Errorf("invalid pattern %q: %w", raw, err)
		}
		p.text = text
		if err := p.compile(); err != nil {
			return nil, fmt.Errorf("invalid pattern %q: %w", raw, err)
		}
		m.patterns = append(m.patterns, p)
	}
	return m, nil
}

// Excluded reports whether the slash-separated context path rel is excluded:
// the last pattern that matches rel or one of its parent directories decides.
func (m *Matcher) Excluded(rel string) bool {
	rel = path.Clean(rel)
	if rel == "." {
		return false
	}
	parent := path.Dir(rel)
	var parents []string
	if parent != "." {
		parents = strings.Split(parent, "/")
	}
	excluded := false
	for _, p := range m.patterns {
		// An inclusion line only matters while the path is included, an
		// exclusion (`!`) line only while it is excluded.
		if p.exclusion != excluded {
			continue
		}
		match := p.match(rel)
		for i := 0; !match && i < len(parents); i++ {
			match = p.match(strings.Join(parents[:i+1], "/"))
		}
		if match {
			excluded = !p.exclusion
		}
	}
	return excluded
}

// MayReinclude reports whether an excluded directory must still be walked
// because a `!` pattern names something inside it.
func (m *Matcher) MayReinclude(dir string) bool {
	if !m.exclusions {
		return false
	}
	prefix := path.Clean(dir) + "/"
	for _, p := range m.patterns {
		if p.exclusion && strings.HasPrefix(p.text+"/", prefix) {
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
		suffix := strings.TrimPrefix(p.text, "**")
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
	runes := []rune(p.text)
	for i := 0; i < len(runes); i++ {
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
			if i <= 2 && strings.HasPrefix(p.text, "**") {
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
