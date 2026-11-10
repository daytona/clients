// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package output

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestParseFormat_refusesUndocumentedSpellings(t *testing.T) {
	for _, value := range []string{"", "JSON", "Yaml", "TABLE", " json", "yaml ", " ", "xml"} {
		t.Run(value, func(t *testing.T) {
			// Given / When
			_, err := ParseFormat(value)
			// Then
			require.Error(t, err)
		})
	}
}

func TestParseFormat_acceptsDocumentedSpellings(t *testing.T) {
	for value, want := range map[string]Format{"table": FormatTable, "json": FormatJSON, "yaml": FormatYAML, "yml": FormatYAML} {
		t.Run(value, func(t *testing.T) {
			// Given / When
			got, err := ParseFormat(value)
			// Then
			require.NoError(t, err)
			require.Equal(t, want, got)
		})
	}
}
