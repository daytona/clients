// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package dockerignore

import (
	"path"
	"strings"
)

// filepath.Match validates only the chunks it reaches against ".". path.Match
// validates later chunks as well; use it on individual chunks so POSIX escapes
// and the same lazy syntax checks also work on Windows.
func validatePattern(pattern string) error {
	remaining, name := pattern, "."
	for remaining != "" {
		star := remaining[0] == '*'
		remaining = strings.TrimLeft(remaining, "*")
		end := chunkEnd(remaining)
		chunk := remaining[:end]
		remaining = remaining[end:]
		if star && chunk == "" {
			return nil
		}
		matched, err := path.Match(chunk, name)
		if err != nil {
			return err
		}
		if matched {
			name = ""
			continue
		}
		if star && name != "" {
			matched, err = path.Match(chunk, "")
			if err != nil {
				return err
			}
			if matched {
				name = ""
				continue
			}
		}
		return nil
	}
	return nil
}

func chunkEnd(pattern string) int {
	inClass := false
	for i := 0; i < len(pattern); i++ {
		switch pattern[i] {
		case '\\':
			if i+1 < len(pattern) {
				i++
			}
		case '[':
			inClass = true
		case ']':
			inClass = false
		case '*':
			if !inClass {
				return i
			}
		}
	}
	return len(pattern)
}
