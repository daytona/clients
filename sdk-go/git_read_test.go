// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestGitStatus_whenRepositoryHasChanges(t *testing.T) {
	// Given
	git, fake := gitTestService(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/git/status": jsonHandler(t, map[string]any{
			"currentBranch": "feature", "ahead": 2, "behind": 1, "branchPublished": true,
			"fileStatus": []map[string]string{{"name": "relative", "staging": "M", "worktree": " ", "extra": ""}},
		}),
	})
	// When
	status, err := git.Status(t.Context(), "/repo with space")
	// Then
	require.NoError(t, err)
	require.Equal(t, &GitStatus{
		CurrentBranch: "feature", Ahead: 2, Behind: 1, BranchPublished: true,
		FileStatus: []GitFileStatus{{Name: "relative", Staging: "M", Worktree: " ", Extra: ""}},
	}, status)
	require.Equal(t, "/repo with space", fake.requests()[0].Query.Get("path"))
}

func TestGitHistory_whenArrayOrEnvelopeIsReturned(t *testing.T) {
	entry := `[{"hash":"sha","message":"message","author":"dev","email":"dev@example.org","timestamp":"2026-01-02T03:04:05Z"}]`
	for _, envelope := range []bool{false, true} {
		t.Run(map[bool]string{false: "array", true: "envelope"}[envelope], func(t *testing.T) {
			// Given
			body := entry
			cursor := ""
			if envelope {
				body, cursor = `{"items":`+entry+`,"nextCursor":"next"}`, "next"
			}
			git, fake := gitTestService(t, map[string]http.HandlerFunc{
				"GET /toolbox/sbx/git/history": jsonHandler(t, json.RawMessage(body)),
			})
			// When
			history, err := git.History(t.Context(), "repo")
			// Then
			require.NoError(t, err)
			require.Equal(t, &Page[GitCommit]{Items: []GitCommit{{
				Hash: "sha", Message: "message", Author: "dev", Email: "dev@example.org",
				Timestamp: time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC),
			}}, NextCursor: cursor}, history)
			require.Len(t, fake.requests(), 1)
			require.Equal(t, "repo", fake.requests()[0].Query.Get("path"))
		})
	}
}

func TestGitLists_whenEntriesAreReturned(t *testing.T) {
	// Given
	git, fake := gitTestService(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/git/branches": jsonHandler(t, map[string][]string{"branches": {"main", "feature"}}),
		"GET /toolbox/sbx/git/remotes": jsonHandler(t, map[string]any{
			"remotes": []map[string]string{{"name": "origin", "url": "https://example.org/repo"}},
		}),
	})
	t.Run("branches", func(t *testing.T) {
		// When
		page, err := git.ListBranches(t.Context(), "repo")
		// Then
		require.NoError(t, err)
		require.Equal(t, []string{"main", "feature"}, page.Items)
	})
	t.Run("remotes", func(t *testing.T) {
		// When
		page, err := git.ListRemotes(t.Context(), "repo")
		// Then
		require.NoError(t, err)
		require.Equal(t, []GitRemote{{Name: "origin", URL: "https://example.org/repo"}}, page.Items)
	})
	for _, request := range fake.requests() {
		require.Equal(t, "repo", request.Query.Get("path"))
	}
}

func TestGitConfig_whenValueIsAbsentOrEmpty(t *testing.T) {
	for _, response := range []string{`{}`, `{"value":null}`, `{"value":""}`, `{"value":"dev"}`} {
		t.Run(response, func(t *testing.T) {
			// Given
			git, fake := gitTestService(t, map[string]http.HandlerFunc{
				"GET /toolbox/sbx/git/config": jsonHandler(t, json.RawMessage(response)),
			})
			// When
			value, err := git.GetConfig(t.Context(), "user.name", &GitConfigOptions{Path: "repo", Scope: GitConfigScopeLocal})
			// Then
			require.NoError(t, err)
			switch response {
			case `{}`, `{"value":null}`:
				require.Nil(t, value)
			case `{"value":""}`:
				require.NotNil(t, value)
				require.Empty(t, *value)
			default:
				require.NotNil(t, value)
				require.Equal(t, "dev", *value)
			}
			query := fake.requests()[0].Query
			require.Equal(t, "user.name", query.Get("key"))
			require.Equal(t, "local", query.Get("scope"))
			require.Equal(t, "repo", query.Get("path"))
		})
	}
}

func TestGitConfig_whenTokenCannotExecute(t *testing.T) {
	// Given: the server refuses a config write without exec permission.
	git, _ := gitTestService(t, map[string]http.HandlerFunc{
		"POST /toolbox/sbx/git/config": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusForbidden)
			writeJSON(t, w, map[string]string{"code": "FORBIDDEN", "source": SourceDaemon, "message": "exec required"})
		},
	})
	// When
	err := git.SetConfig(t.Context(), "user.name", "new", nil)
	// Then
	require.ErrorIs(t, err, ErrForbidden)
}
