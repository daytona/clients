// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSandboxLabelsPreservesFreshName(t *testing.T) {
	// Given
	detail := sandboxFixture("sbx")
	detail["labels"] = map[string]string{"name": "fresh", "old": "removed"}
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx":        jsonHandler(t, detail),
		"PUT /sandboxes/sbx/labels": jsonHandler(t, map[string]any{"labels": map[string]string{"name": "fresh", "team": "sdk"}}),
	})
	sandbox := newMutatorSandbox(t, fake)
	sandbox.info.Labels = map[string]string{"name": "cached"}
	input := map[string]string{"team": "sdk"}
	// When
	applied, err := sandbox.SetLabels(t.Context(), input)
	// Then
	require.NoError(t, err)
	require.Len(t, fake.requests(), 2)
	require.JSONEq(t, `{"labels":{"name":"fresh","team":"sdk"}}`, string(fake.requests()[1].Body))
	require.Equal(t, map[string]string{"team": "sdk"}, input)
	require.Equal(t, applied, sandbox.Info().Labels)
	applied["name"] = "mutated"
	require.Equal(t, "fresh", sandbox.Info().Labels["name"])
}

func TestSandboxLabelsExplicitNameSkipsRead(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"PUT /sandboxes/sbx/labels": jsonHandler(t, map[string]any{"labels": map[string]string{"name": "new"}}),
	})
	sandbox := newMutatorSandbox(t, fake)
	// When
	applied, err := sandbox.SetLabels(t.Context(), map[string]string{"name": "new"})
	// Then
	require.NoError(t, err)
	require.Equal(t, map[string]string{"name": "new"}, applied)
	require.Len(t, fake.requests(), 1)
}

func TestSandboxLabelsClearsWhenFreshReadHasNoName(t *testing.T) {
	// Given
	detail := sandboxFixture("sbx")
	delete(detail, "labels")
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx":        jsonHandler(t, detail),
		"PUT /sandboxes/sbx/labels": jsonHandler(t, map[string]any{"labels": map[string]string{}}),
	})
	sandbox := newMutatorSandbox(t, fake)
	sandbox.info.Labels = map[string]string{"name": "stale"}
	// When
	_, err := sandbox.SetLabels(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.JSONEq(t, `{"labels":{}}`, string(fake.requests()[1].Body))
	require.Empty(t, sandbox.Info().Labels)
}

func TestSandboxLabelsRejectsEmptyKey(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	sandbox := newMutatorSandbox(t, fake)
	// When
	_, err := sandbox.SetLabels(t.Context(), map[string]string{"": "bad"})
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}

func TestSandboxLabelsRejectsMalformedEcho(t *testing.T) {
	for name, body := range map[string]any{
		"missing": map[string]any{}, "null": map[string]any{"labels": nil},
		"array":      map[string]any{"labels": []string{}},
		"null value": map[string]any{"labels": map[string]any{"name": nil}},
		"number":     map[string]any{"labels": map[string]any{"name": 1}},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"PUT /sandboxes/sbx/labels": jsonHandler(t, body)})
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.SetLabels(t.Context(), map[string]string{"name": "new"})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Nil(t, sandbox.Info().Labels)
		})
	}
}
