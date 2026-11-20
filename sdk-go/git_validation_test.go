// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestGitInput_whenInvalid(t *testing.T) {
	for _, test := range []struct {
		name string
		call func(context.Context, *Git) error
	}{
		{"clone depth", func(ctx context.Context, g *Git) error {
			return g.Clone(ctx, "url", "repo", &GitCloneOptions{Depth: -1})
		}},
		{"clone timeout", func(ctx context.Context, g *Git) error {
			return g.Clone(ctx, "url", "repo", &GitCloneOptions{Timeout: -time.Second})
		}},
		{"pull timeout", func(ctx context.Context, g *Git) error {
			return g.Pull(ctx, "repo", &GitCredentialOptions{Timeout: NoWait})
		}},
		{"push timeout", func(ctx context.Context, g *Git) error {
			return g.Push(ctx, "repo", &GitPushOptions{GitCredentialOptions: GitCredentialOptions{Timeout: WaitForever}})
		}},
		{"ambiguous credentials", func(ctx context.Context, g *Git) error {
			return g.Pull(ctx, "repo", &GitCredentialOptions{Token: "token", Password: "password"})
		}},
		{"commit absent identity", func(ctx context.Context, g *Git) error {
			_, err := g.Commit(ctx, "repo", "message", nil)
			return err
		}},
		{"commit blank author", func(ctx context.Context, g *Git) error {
			_, err := g.Commit(ctx, "repo", "message", &GitCommitOptions{Author: " ", Email: "e"})
			return err
		}},
		{"commit blank email", func(ctx context.Context, g *Git) error {
			_, err := g.Commit(ctx, "repo", "message", &GitCommitOptions{Author: "a", Email: "\n"})
			return err
		}},
		{"get scope", func(ctx context.Context, g *Git) error {
			_, err := g.GetConfig(ctx, "key", &GitConfigOptions{Scope: "system"})
			return err
		}},
		{"set scope", func(ctx context.Context, g *Git) error {
			return g.SetConfig(ctx, "key", "value", &GitConfigOptions{Scope: "system"})
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			git, fake := gitTestService(t, nil)
			// When
			err := test.call(t.Context(), git)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			var sdk *Error
			require.ErrorAs(t, err, &sdk)
			require.Equal(t, SourceSDK, sdk.Source)
			require.Empty(t, fake.requests())
		})
	}
}

func TestGitResponse_whenMalformed(t *testing.T) {
	for _, test := range []struct {
		path, body string
		call       func(context.Context, *Git) error
	}{
		{"status", `{}`, func(ctx context.Context, g *Git) error { _, e := g.Status(ctx, "repo"); return e }},
		{
			"status", `{"currentBranch":"m","ahead":1.2,"behind":0,"branchPublished":true,"fileStatus":[]}`,
			func(ctx context.Context, g *Git) error { _, e := g.Status(ctx, "repo"); return e },
		},
		{
			"status", `{"currentBranch":"m","ahead":0,"behind":0,"branchPublished":true,"fileStatus":[{"name":"f","staging":"M","worktree":" "}]}`,
			func(ctx context.Context, g *Git) error { _, e := g.Status(ctx, "repo"); return e },
		},
		{"branches", `{"branches":[null]}`, func(ctx context.Context, g *Git) error { _, e := g.ListBranches(ctx, "repo"); return e }},
		{"branches", `{"branches":null}`, func(ctx context.Context, g *Git) error { _, e := g.ListBranches(ctx, "repo"); return e }},
		{"remotes", `{"remotes":[null]}`, func(ctx context.Context, g *Git) error { _, e := g.ListRemotes(ctx, "repo"); return e }},
		{"remotes", `{"remotes":[{"name":"origin","url":1}]}`, func(ctx context.Context, g *Git) error { _, e := g.ListRemotes(ctx, "repo"); return e }},
		{
			"history", `[{"hash":"h","message":"m","author":"a","email":"e","timestamp":"bad"}]`,
			func(ctx context.Context, g *Git) error { _, e := g.History(ctx, "repo"); return e },
		},
		{"history", `{"items":[],"nextCursor":1}`, func(ctx context.Context, g *Git) error { _, e := g.History(ctx, "repo"); return e }},
		{"history", `null`, func(ctx context.Context, g *Git) error { _, e := g.History(ctx, "repo"); return e }},
		{"config", `{"value":1}`, func(ctx context.Context, g *Git) error { _, e := g.GetConfig(ctx, "key", nil); return e }},
		{"commit", `{}`, func(ctx context.Context, g *Git) error {
			_, e := g.Commit(ctx, "repo", "m", &GitCommitOptions{Author: "a", Email: "e"})
			return e
		}},
	} {
		t.Run(test.path+test.body, func(t *testing.T) {
			// Given
			git, _ := gitTestService(t, map[string]http.HandlerFunc{
				"/toolbox/sbx/git/" + test.path: jsonHandler(t, json.RawMessage(test.body)),
			})
			// When
			err := test.call(t.Context(), git)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestGitFailure_whenServerReturnsCode(t *testing.T) {
	for _, test := range []struct {
		code   string
		status int
		want   error
	}{
		{"GIT_AUTH_FAILED", 401, ErrGitAuthFailed},
		{"GIT_REPO_NOT_FOUND", 404, ErrGitRepoNotFound},
		{"GIT_BRANCH_NOT_FOUND", 404, ErrGitBranchNotFound},
		{"GIT_BRANCH_EXISTS", 409, ErrGitBranchExists},
		{"CONFLICT", 409, ErrConflict},
		{"FORBIDDEN", 403, ErrForbidden},
	} {
		t.Run(test.code, func(t *testing.T) {
			// Given
			git, fake := gitTestService(t, map[string]http.HandlerFunc{
				"DELETE /toolbox/sbx/git/branches": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(test.status)
					writeJSON(t, w, map[string]string{"code": test.code, "source": SourceDaemon, "message": "failure"})
				},
			})
			// When
			err := git.DeleteBranch(t.Context(), "repo", "feature", nil)
			// Then
			require.ErrorIs(t, err, test.want)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestGit_whenContextIsCancelled(t *testing.T) {
	// Given
	git, fake := gitTestService(t, nil)
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	// When
	err := git.Pull(ctx, "repo", nil)
	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.Empty(t, fake.requests())
}
