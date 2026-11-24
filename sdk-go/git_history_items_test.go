// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestGitHistory_whenArrayHoldsNonObjects_namesTheItems(t *testing.T) {
	// Given a history response that is a list, but not of commit objects
	git, _ := gitTestService(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/git/history": jsonHandler(t, json.RawMessage(`[{"hash":"sha"}, 7]`)),
	})
	// When
	_, err := git.History(t.Context(), "repo")
	// Then the error describes the list, not a missing object
	require.ErrorIs(t, err, ErrBadRequest)
	require.Contains(t, err.Error(), "git history response items must be objects")
}
