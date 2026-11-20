// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

// Count the actual generated switch entries, independently of the TS regexes.
func generatedCatalogCodes(t *testing.T) map[string]struct{} {
	t.Helper()
	files, err := filepath.Glob("codes_catalog_*.go")
	require.NoError(t, err)
	codes := make(map[string]struct{})
	for _, file := range files {
		parsed, parseErr := parser.ParseFile(token.NewFileSet(), file, nil, 0)
		require.NoError(t, parseErr)
		ast.Inspect(parsed, func(node ast.Node) bool {
			clause, ok := node.(*ast.CaseClause)
			if !ok {
				return true
			}
			for _, expression := range clause.List {
				literal, isLiteral := expression.(*ast.BasicLit)
				require.True(t, isLiteral, "catalog case must be a string literal")
				code, unquoteErr := strconv.Unquote(literal.Value)
				require.NoError(t, unquoteErr)
				require.NotContains(t, codes, code, "duplicate catalog entry")
				codes[code] = struct{}{}
			}
			return false
		})
	}
	require.NotEmpty(t, codes)
	return codes
}
