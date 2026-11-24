// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package dockerignore

import (
	"path"
	"strings"
)

// Matcher is an immutable, concurrency-safe set of ordered ignore patterns.
type Matcher struct {
	patterns           []pattern
	wildcardExceptions bool
}

// New validates pattern syntax. Regular-expression errors are deferred until
// the affected pattern is evaluated, matching BuildKit's exclusion behavior.
func New(patterns []string) (*Matcher, error) {
	m := &Matcher{}
	for _, raw := range patterns {
		text := strings.TrimSpace(raw)
		if text == "" {
			continue
		}
		text = path.Clean(text)
		exclusion := strings.HasPrefix(text, "!")
		if text == "!" {
			return nil, patternError(raw, "illegal exclusion pattern")
		}
		if exclusion {
			text = text[1:]
		}
		if err := validatePattern(text); err != nil {
			return nil, patternError(raw, "syntax error in pattern")
		}
		p := compilePattern(text)
		p.exclusion = exclusion
		if p.err != nil {
			p.err = patternError(raw, "invalid regular expression")
		}
		m.patterns = append(m.patterns, p)
		if exclusion && strings.ContainsAny(withoutTrailingGlob(text), "*[]?^\\") {
			m.wildcardExceptions = true
		}
	}
	return m, nil
}

// Results carries per-pattern parent matches to a child path.
type Results []bool

// Match evaluates a relative POSIX path, optionally reusing its parent's results.
func (m *Matcher) Match(relative string, parent Results) (bool, Results, error) {
	rel := path.Clean(relative)
	matched := make(Results, len(m.patterns))
	known := len(parent) == len(m.patterns) && len(parent) != 0
	excluded := false
	for i, p := range m.patterns {
		match := known && parent[i]
		if !match {
			if p.exclusion != excluded {
				continue
			}
			if p.err != nil {
				return false, nil, p.err
			}
			match = p.matches(rel)
			if !match && !known {
				match = p.matchesParent(rel)
			}
		}
		matched[i] = match
		if match {
			excluded = !p.exclusion
		}
	}
	return excluded, matched, nil
}

func (p pattern) matchesParent(rel string) bool {
	for i := range len(rel) {
		if rel[i] == '/' && p.matches(rel[:i]) {
			return true
		}
	}
	return false
}

// Excluded evaluates a path with all of its parents.
func (m *Matcher) Excluded(relative string) (bool, error) {
	rel := path.Clean(relative)
	if rel == "." {
		return false, nil
	}
	var state Results
	var excluded bool
	var err error
	for i := range len(rel) + 1 {
		if i != len(rel) && rel[i] != '/' {
			continue
		}
		excluded, state, err = m.Match(rel[:i], state)
		if err != nil {
			return false, err
		}
	}
	return excluded, nil
}

func withoutTrailingGlob(text string) string {
	return strings.TrimSuffix(strings.TrimSuffix(text, "/**"), "/*")
}

// MayReinclude reports whether a negation can keep a descendant of directory.
func (m *Matcher) MayReinclude(directory string) bool {
	if m.wildcardExceptions {
		return true
	}
	prefix := path.Clean(directory) + "/"
	for _, p := range m.patterns {
		if p.exclusion && strings.HasPrefix(withoutTrailingGlob(p.text)+"/", prefix) {
			return true
		}
	}
	return false
}

// Filter combines ignore patterns with literal paths that must be kept.
type Filter struct {
	matcher    *Matcher
	always     map[string]bool
	alwaysDirs map[string]bool
}

// NewFilter retains always-kept paths and the ancestors needed to reach them.
func NewFilter(matcher *Matcher, always []string) *Filter {
	f := &Filter{matcher: matcher, always: make(map[string]bool), alwaysDirs: make(map[string]bool)}
	for _, name := range always {
		name = path.Clean(name)
		f.always[name] = true
		for dir := path.Dir(name); dir != "." && dir != "/"; dir = path.Dir(dir) {
			f.alwaysDirs[dir] = true
		}
	}
	return f
}

// Decision selects an entry and controls descent into a directory.
type Decision struct {
	// Keep includes the entry.
	Keep bool
	// Descend visits children even when the directory itself is excluded.
	Descend bool
	// State is the pattern state for children.
	State Results
}

// Keep evaluates a path even if it is always kept, preserving delayed errors.
func (f *Filter) Keep(rel string, parent Results) (Decision, error) {
	excluded, state, err := f.matcher.Match(rel, parent)
	if err != nil {
		return Decision{}, err
	}
	keep := !excluded || f.always[rel]
	return Decision{
		Keep: keep, Descend: keep || f.alwaysDirs[rel] || f.matcher.MayReinclude(rel), State: state,
	}, nil
}
