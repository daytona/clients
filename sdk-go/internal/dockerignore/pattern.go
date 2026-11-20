// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Package dockerignore implements POSIX build-context ignore rules.
// Its implementation is derived from the Apache-licensed SDKs and shared
// behavioral fixtures, without using the CLI implementation.
package dockerignore

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"path"
	"regexp"
	"strings"
	"unicode/utf8"
)

// Error is an invalid ignore-file line or pattern.
type Error struct {
	// Message describes the invalid input.
	Message string
}

// Error describes the invalid ignore input.
func (e *Error) Error() string { return e.Message }

func patternError(raw, reason string) error {
	quoted, err := json.Marshal(raw)
	if err != nil {
		return &Error{Message: "invalid pattern: " + reason}
	}
	return &Error{Message: "invalid pattern " + string(quoted) + ": " + reason}
}

// ReadPatterns reads Docker ignore-file text, including BOM and negation rules.
// Lines of 64 KiB or more after UTF-8 replacement return *Error.
func ReadPatterns(text string) ([]string, error) {
	return ReadPatternsFrom(strings.NewReader(text))
}

// ReadPatternsFrom incrementally reads ignore-file lines, bounding each line
// to less than 64 KiB after UTF-8 replacement, before BOM or whitespace removal.
// Reader failures, including cancellation, are preserved.
func ReadPatternsFrom(reader io.Reader) ([]string, error) {
	var patterns []string
	scanner := bufio.NewScanner(reader)
	// Split only on LF: a trailing CR counts toward the decoded line limit.
	scanner.Split(func(data []byte, atEOF bool) (int, []byte, error) {
		if index := bytes.IndexByte(data, '\n'); index >= 0 {
			return index + 1, data[:index], nil
		}
		if atEOF && len(data) > 0 {
			return len(data), data, nil
		}
		return 0, nil, nil
	})
	first := true
	for scanner.Scan() {
		line := decodePatternText(scanner.Text())
		if len(line) >= 64*1024 {
			return nil, &Error{Message: "dockerignore line exceeds 64 KiB"}
		}
		if first {
			line = strings.TrimPrefix(line, "\ufeff")
			first = false
		}
		if strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		invert := strings.HasPrefix(line, "!")
		if invert {
			line = strings.TrimSpace(line[1:])
		}
		if line != "" {
			line = path.Clean(line)
			if len(line) > 1 {
				line = strings.TrimPrefix(line, "/")
			}
		}
		if invert {
			line = "!" + line
		}
		patterns = append(patterns, line)
	}
	err := scanner.Err()
	if errors.Is(err, bufio.ErrTooLong) {
		return nil, &Error{Message: "dockerignore line exceeds 64 KiB"}
	}
	if err != nil {
		return nil, err
	}
	return patterns, nil
}

// decodePatternText replaces each maximal malformed UTF-8 subpart separately,
// as Python/TypeScript do, rather than collapsing adjacent malformed sequences.
func decodePatternText(text string) string {
	var decoded strings.Builder
	decoded.Grow(len(text))
	for offset := 0; offset < len(text); {
		char, width := utf8.DecodeRuneInString(text[offset:])
		if char == utf8.RuneError && width == 1 {
			// FullRune reports incomplete valid prefixes as false, and invalid
			// encodings as true. Consume only the incomplete prefix, not the
			// following byte that makes the sequence invalid.
			for offset+width < len(text) && !utf8.FullRuneInString(text[offset:offset+width+1]) {
				width++
			}
		}
		decoded.WriteRune(char)
		offset += width
	}
	return decoded.String()
}

type patternKind uint8

const (
	exactPattern patternKind = iota
	prefixPattern
	suffixPattern
	regexpPattern
)

type pattern struct {
	text      string
	exclusion bool
	kind      patternKind
	expr      *regexp.Regexp
	err       error
}

func (p pattern) matches(name string) bool {
	switch p.kind {
	case exactPattern:
		return p.text == name
	case prefixPattern:
		return strings.HasPrefix(name, strings.TrimSuffix(p.text, "**"))
	case suffixPattern:
		suffix := p.text[2:] // Deliberately byte-based, including a retained BOM.
		return strings.HasSuffix(name, suffix) || strings.HasPrefix(suffix, "/") && name == suffix[1:]
	case regexpPattern:
		return p.expr.MatchString(name)
	}
	return false
}

func compilePattern(text string) pattern {
	p := pattern{text: text, kind: exactPattern}
	var expression strings.Builder
	expression.WriteByte('^')
	runes := []rune(strings.TrimPrefix(text, "\ufeff"))
	for i, token := 0, 0; i < len(runes); i, token = i+1, token+1 {
		ch := runes[i]
		switch {
		case ch == '*' && i+1 < len(runes) && runes[i+1] == '*':
			i++
			if i+1 < len(runes) && runes[i+1] == '/' {
				i++
			}
			p.doubleStar(&expression, i+1 == len(runes), token == 0)
		case ch == '*':
			expression.WriteString("[^/]*")
			p.kind = regexpPattern
		case ch == '?':
			expression.WriteString("[^/]")
			p.kind = regexpPattern
		case strings.ContainsRune(".+()|{}$", ch):
			expression.WriteByte('\\')
			expression.WriteRune(ch)
		case ch == '\\':
			expression.WriteByte('\\')
			if i+1 < len(runes) {
				i++
				expression.WriteRune(runes[i])
				p.kind = regexpPattern
			} else {
				expression.WriteByte('\\')
			}
		default:
			expression.WriteRune(ch)
			if ch == '[' || ch == ']' {
				p.kind = regexpPattern
			}
		}
	}
	if p.kind == regexpPattern {
		expression.WriteByte('$')
		p.expr, p.err = regexp.Compile(expression.String())
	}
	return p
}

func (p *pattern) doubleStar(expression *strings.Builder, last, first bool) {
	if last && p.kind == exactPattern {
		p.kind = prefixPattern
	} else if last {
		expression.WriteString(".*")
		p.kind = regexpPattern
	} else {
		expression.WriteString("(.*/)?")
		p.kind = regexpPattern
	}
	if first {
		p.kind = suffixPattern
	}
}
