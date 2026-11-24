// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

func builderSpace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }

func builderTrim(value string) string { return strings.TrimFunc(value, builderSpace) }

func builderText(value, label string) error {
	if builderTrim(value) == "" {
		return badRequest(label + " must be a non-empty string")
	}
	if !utf8.ValidString(value) {
		return badRequest(label + " must be valid UTF-8")
	}
	return nil
}

func builderControl(r rune) bool { return r < 32 || r == 127 }

func builderLine(value, label string) (string, error) {
	if err := builderText(value, label); err != nil {
		return "", err
	}
	text := builderTrim(value)
	if strings.ContainsFunc(text, builderControl) {
		return "", badRequest(label + " must not contain a control character")
	}
	if strings.HasSuffix(text, "\\") {
		return "", badRequest(label + " must not end with a backslash")
	}
	return text, nil
}

func (b *ImageBuilder) rawLine(value, label string) (string, error) {
	text, err := builderLine(value, label)
	if err != nil {
		return "", err
	}
	recipe := b.snapshot()
	escape := builderEscape(recipe.base)
	if recipe.raw && escape != "\\" && builderContinuation("# escape="+escape+"\nRUN "+text) {
		return "", badRequest(label + " ends with the Dockerfile's escape character")
	}
	return text, nil
}

// JSON strings preserve Unicode exactly as JSON.stringify does. In particular,
// Go's optional HTML and U+2028/U+2029 escapes would change the archive digest.
func builderQuote(value string) string {
	var result strings.Builder
	result.WriteByte('"')
	for _, r := range value {
		switch r {
		case '"', '\\':
			result.WriteByte('\\')
			result.WriteRune(r)
		case '\b':
			result.WriteString(`\b`)
		case '\f':
			result.WriteString(`\f`)
		case '\n':
			result.WriteString(`\n`)
		case '\r':
			result.WriteString(`\r`)
		case '\t':
			result.WriteString(`\t`)
		default:
			if r < 32 {
				hex := strconv.FormatInt(int64(r), 16)
				result.WriteString(`\u` + strings.Repeat("0", 4-len(hex)) + hex)
			} else {
				result.WriteRune(r)
			}
		}
	}
	result.WriteByte('"')
	return result.String()
}

func builderArray(values []string) string {
	quoted := make([]string, len(values))
	for i, value := range values {
		quoted[i] = builderQuote(value)
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}

func builderShellQuote(value string) string {
	safe := value != "" && !strings.ContainsFunc(value, func(r rune) bool {
		allowed := r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' ||
			strings.ContainsRune("@%+=:,./_-", r)
		return !allowed
	})
	if safe {
		return value
	}
	return "'" + strings.ReplaceAll(value, "'", `'"'"'`) + "'"
}
