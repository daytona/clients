// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestComputerUseA11yRejectsMalformedNodeFields(t *testing.T) {
	type malformedNode struct {
		name   string
		mutate func(map[string]any)
	}
	tests := make([]malformedNode, 0, 28)
	for _, name := range []string{"id", "role", "name", "bounds", "states"} {
		tests = append(tests,
			malformedNode{"missing " + name, func(node map[string]any) { delete(node, name) }},
			malformedNode{"null " + name, func(node map[string]any) { node[name] = nil }},
		)
	}
	for _, test := range []struct {
		name  string
		value any
	}{
		{"id", 1},
		{"role", false},
		{"name", []any{}},
		{"description", 1},
		{"value", false},
		{"valueTruncated", "true"},
		{"bounds", []any{}},
		{"states", map[string]any{}},
		{"states", []any{nil}},
		{"states", []any{1}},
		{"actions", ""},
		{"actions", []any{false}},
		{"actions", []any{nil}},
		{"children", map[string]any{}},
		{"children", ""},
		{"children", 42},
		{"children", []any{nil}},
		{"children", []any{map[string]any{}}},
	} {
		tests = append(tests, malformedNode{"invalid " + test.name, func(node map[string]any) { node[test.name] = test.value }})
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given
			root := computerUseA11yNodeFixture("root")
			test.mutate(root)
			sandbox, _ := computerUseA11yTestSandbox(t, jsonHandler(t, map[string]any{"root": root, "truncated": false}))

			// When
			_, err := sandbox.ComputerUse().A11y().Tree(t.Context(), ComputerUseA11yScopeFocused, nil)

			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, "SDK", sdkErr.Source)
		})
	}
}

func TestComputerUseA11yRejectsMalformedBounds(t *testing.T) {
	for _, name := range []string{"x", "y", "width", "height"} {
		for _, value := range []any{nil, "1", true, 0.5, -2147483649, 2147483648} {
			t.Run(name, func(t *testing.T) {
				// Given
				root := computerUseA11yNodeFixture("root")
				bounds := map[string]any{"x": 0, "y": 0, "width": 1, "height": 1}
				bounds[name] = value
				root["bounds"] = bounds
				sandbox, _ := computerUseA11yTestSandbox(t, jsonHandler(t, map[string]any{"root": root, "truncated": false}))

				// When
				_, err := sandbox.ComputerUse().A11y().Tree(t.Context(), ComputerUseA11yScopeFocused, nil)

				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.ErrorContains(t, err, name)
			})
		}
	}
}

func TestComputerUseA11yRejectsMalformedTreeEnvelopes(t *testing.T) {
	for _, body := range []any{
		nil,
		[]any{},
		"tree",
		map[string]any{},
		map[string]any{"root": nil, "truncated": false},
		map[string]any{"root": 1, "truncated": false},
		map[string]any{"root": computerUseA11yNodeFixture("n")},
		map[string]any{"root": computerUseA11yNodeFixture("n"), "truncated": nil},
		map[string]any{"root": computerUseA11yNodeFixture("n"), "truncated": 0},
	} {
		t.Run("invalid tree", func(t *testing.T) {
			// Given
			sandbox, _ := computerUseA11yTestSandbox(t, jsonHandler(t, body))

			// When
			_, err := sandbox.ComputerUse().A11y().Tree(t.Context(), ComputerUseA11yScopeFocused, nil)

			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestComputerUseA11yRejectsMalformedFindEnvelopes(t *testing.T) {
	for _, body := range []any{
		nil,
		[]any{},
		"find",
		map[string]any{},
		map[string]any{"truncated": false},
		map[string]any{"matches": nil, "truncated": false},
		map[string]any{"matches": "", "truncated": false},
		map[string]any{"matches": map[string]any{}, "truncated": false},
		map[string]any{"matches": []any{nil}, "truncated": false},
		map[string]any{"matches": []any{map[string]any{}}, "truncated": false},
		map[string]any{"matches": []any{}},
		map[string]any{"matches": []any{}, "truncated": nil},
		map[string]any{"matches": []any{}, "truncated": "false"},
	} {
		t.Run("invalid find", func(t *testing.T) {
			// Given
			sandbox, _ := computerUseA11yTestSandbox(t, jsonHandler(t, body))

			// When
			_, err := sandbox.ComputerUse().A11y().Find(t.Context(), ComputerUseA11yScopeAll, nil)

			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
