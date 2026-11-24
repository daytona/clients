// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func computerUseA11yChain(depth int) map[string]any {
	root := computerUseA11yNodeFixture("leaf")
	for range depth {
		parent := computerUseA11yNodeFixture("parent")
		parent["children"] = []any{root}
		root = parent
	}
	return root
}

func TestComputerUseA11yTreeDepthBoundaries(t *testing.T) {
	zero, two := 0, 2
	for _, test := range []struct {
		name     string
		depth    int
		maxDepth *int
		query    string
		accepted bool
	}{
		{"default accepts cap", 256, nil, "256", true},
		{"default rejects excess", 257, nil, "256", false},
		{"explicit zero accepts root", 0, &zero, "0", true},
		{"explicit zero rejects child", 1, &zero, "0", false},
		{"explicit depth accepts boundary", 2, &two, "2", true},
		{"explicit depth rejects excess", 3, &two, "2", false},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			body := map[string]any{"root": computerUseA11yChain(test.depth), "truncated": true}
			sandbox, fake := computerUseA11yTestSandbox(t, jsonHandler(t, body))
			var opts *ComputerUseA11yTreeOptions
			if test.maxDepth != nil {
				opts = &ComputerUseA11yTreeOptions{MaxDepth: test.maxDepth}
			}

			// When
			tree, err := sandbox.ComputerUse().A11y().Tree(t.Context(), ComputerUseA11yScopeFocused, opts)

			// Then
			requests := fake.requests()
			require.Equal(t, test.query, requests[len(requests)-1].Query.Get("maxDepth"))
			if !test.accepted {
				require.ErrorIs(t, err, ErrBadRequest)
				return
			}
			require.NoError(t, err)
			depth := 0
			for node := tree.Root; len(node.Children) > 0; node = node.Children[0] {
				depth++
			}
			require.Equal(t, test.depth, depth)
		})
	}
}

func TestComputerUseA11yTreeRejectsInvalidDepthBeforeRequest(t *testing.T) {
	for _, depth := range []int{-1, 257} {
		t.Run(strconv.Itoa(depth), func(t *testing.T) {
			// Given
			sandbox, fake := computerUseA11yTestSandbox(t, jsonHandler(t, map[string]any{}))
			before := len(fake.requests())

			// When
			_, err := sandbox.ComputerUse().A11y().Tree(t.Context(), ComputerUseA11yScopeFocused, &ComputerUseA11yTreeOptions{MaxDepth: &depth})

			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, "SDK", sdkErr.Source)
			require.Equal(t, "BAD_REQUEST", sdkErr.Code)
			require.Len(t, fake.requests(), before)
		})
	}
}

func TestComputerUseA11yFindDepthBoundaries(t *testing.T) {
	for _, depth := range []int{256, 257} {
		t.Run(strconv.Itoa(depth), func(t *testing.T) {
			// Given
			body := map[string]any{"matches": []any{computerUseA11yChain(depth)}, "truncated": false}
			sandbox, _ := computerUseA11yTestSandbox(t, jsonHandler(t, body))

			// When
			found, err := sandbox.ComputerUse().A11y().Find(t.Context(), ComputerUseA11yScopeAll, nil)

			// Then
			if depth > 256 {
				require.ErrorIs(t, err, ErrBadRequest)
				return
			}
			require.NoError(t, err)
			require.Len(t, found.Matches, 1)
		})
	}
}
