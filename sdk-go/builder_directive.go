// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"strings"
	"unicode"
)

func builderScanned(raw string, index int) string {
	if index == 0 {
		raw = strings.TrimPrefix(raw, "\ufeff")
	}
	return strings.TrimRight(raw, "\r\n")
}

func builderDirective(text string) (string, string, bool) {
	end := 0
	for i, r := range text {
		valid := r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || i > 0 && r >= '0' && r <= '9'
		if !valid {
			break
		}
		end = i + 1
	}
	if end == 0 {
		return "", "", false
	}
	rest := strings.TrimLeft(text[end:], "\t\n\f\r ")
	if !strings.HasPrefix(rest, "=") || len(rest) == 1 {
		return "", "", false
	}
	value := strings.Trim(rest[1:], "\t\n\f\r ")
	if value == "" {
		value = rest[len(rest)-1:]
	}
	return strings.ToLower(text[:end]), value, true
}

func builderEscape(dockerfile string) string {
	escape := "\\"
	seen := make(map[string]bool)
	for index, raw := range strings.Split(dockerfile, "\n") {
		line := strings.TrimLeftFunc(builderScanned(raw, index), unicode.IsSpace)
		if !strings.HasPrefix(line, "#") {
			break
		}
		name, value, ok := builderDirective(strings.TrimLeftFunc(line[1:], unicode.IsSpace))
		if !ok || seen[name] || name != "syntax" && name != "escape" && name != "check" {
			break
		}
		seen[name] = true
		if name == "escape" {
			escape = value
		}
	}
	return escape
}

func builderContinuation(dockerfile string) bool {
	escape := builderEscape(dockerfile)
	if escape != "\\" && escape != "`" {
		return false
	}
	pending, carried := false, false
	for index, raw := range strings.Split(dockerfile, "\n") {
		line := builderScanned(raw, index)
		stripped := strings.TrimLeftFunc(line, unicode.IsSpace)
		if strings.HasPrefix(stripped, "#") || pending && stripped == "" {
			continue
		}
		text := stripped
		if pending {
			text = line
		}
		text = strings.TrimRight(text, " \t")
		matched := strings.HasSuffix(text, escape) && !strings.HasSuffix(text, escape+escape)
		added := text
		if matched {
			added = text[:len(text)-1]
		}
		carried = carried && pending || strings.TrimSpace(added) != ""
		pending = matched
	}
	return pending && carried
}
