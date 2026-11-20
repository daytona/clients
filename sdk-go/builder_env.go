// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"maps"
	"slices"
	"strings"
)

// Env adds a literal environment variable, escaping dollars, quotes and
// backslashes. Names are trimmed; values are preserved. Blank names/values,
// controls, whitespace or '=' in names, and non-default Dockerfile escape
// directives fail compilation with ErrBadRequest. Errors never echo values.
func (b *ImageBuilder) Env(name, value string) *ImageBuilder {
	line, err := builderEnv(name, value)
	if err != nil {
		return b.appendSteps(nil, err)
	}
	return b.envSteps([]string{line})
}

// EnvVars adds literal variables in sorted key order for deterministic output.
// The map is snapshotted. Empty maps add nothing. Keys colliding after trimming
// and values invalid under Env's rules fail compilation with ErrBadRequest.
func (b *ImageBuilder) EnvVars(values map[string]string) *ImageBuilder {
	keys := slices.Sorted(maps.Keys(values))
	lines := make([]string, 0, len(keys))
	seen := make(map[string]bool, len(keys))
	for _, key := range keys {
		line, err := builderEnv(key, values[key])
		if err != nil {
			return b.appendSteps(nil, err)
		}
		name := builderTrim(key)
		if seen[name] {
			return b.appendSteps(nil, badRequest("environment key appears more than once after trimming: "+name))
		}
		seen[name] = true
		lines = append(lines, line)
	}
	return b.envSteps(lines)
}

func builderEnv(key, value string) (string, error) {
	if err := builderText(key, "environment name"); err != nil {
		return "", err
	}
	name := builderTrim(key)
	if strings.ContainsFunc(name, func(r rune) bool { return builderSpace(r) || builderControl(r) || r == '=' }) {
		return "", badRequest("environment name must not contain '=', whitespace or a control character")
	}
	if err := builderText(value, "environment value"); err != nil {
		return "", err
	}
	if strings.ContainsFunc(value, builderControl) {
		return "", badRequest("environment value must not contain a control character")
	}
	return "ENV " + name + "=" + strings.ReplaceAll(builderQuote(value), "$", `\$`), nil
}

func (b *ImageBuilder) envSteps(lines []string) *ImageBuilder {
	recipe := b.snapshot()
	if recipe.raw && builderEscape(recipe.base) != "\\" {
		return b.appendSteps(nil, badRequest("environment values need the default Dockerfile escape character"))
	}
	return b.appendSteps(lines, nil)
}
