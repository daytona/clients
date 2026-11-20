// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func gitTestService(t *testing.T, routes map[string]http.HandlerFunc) (*Git, *fakeServer) {
	t.Helper()
	fake := newFakeServer(t, routes)
	client := newTestClient(t, fake.Server)
	sandbox, err := client.newSandbox(sandboxInit{
		Info: SandboxInfo{ID: "sbx"}, ToolboxURL: fake.Server.URL + "/toolbox",
		Options: GetSandboxOptions{AccessToken: "caller"},
	})
	require.NoError(t, err)
	return sandbox.Git(), fake
}

func TestGitMutations_whenOptionsAreProvided(t *testing.T) {
	yes, no := true, false
	for _, test := range []struct {
		name, method, path, body string
		call                     func(context.Context, *Git) error
	}{
		{
			"init defaults", "POST", "/git/init", `{"path":"repo","bare":false}`,
			func(ctx context.Context, g *Git) error { return g.Init(ctx, "repo", nil) },
		},
		{
			"init", "POST", "/git/init", `{"path":"repo","bare":true,"initial_branch":"trunk"}`,
			func(ctx context.Context, g *Git) error {
				return g.Init(ctx, "repo", &GitInitOptions{Bare: true, InitialBranch: "trunk"})
			},
		},
		{
			"add", "POST", "/git/add", `{"path":"repo","files":["relative","*.go"]}`,
			func(ctx context.Context, g *Git) error { return g.Add(ctx, "repo", []string{"relative", "*.go"}) },
		},
		{
			"add empty", "POST", "/git/add", `{"path":"repo","files":[]}`,
			func(ctx context.Context, g *Git) error { return g.Add(ctx, "repo", nil) },
		},
		{
			"reset defaults", "POST", "/git/reset", `{"path":"repo"}`,
			func(ctx context.Context, g *Git) error { return g.Reset(ctx, "repo", nil) },
		},
		{
			"reset", "POST", "/git/reset", `{"path":"repo","mode":"mixed","target":"HEAD~1","files":["relative"]}`,
			func(ctx context.Context, g *Git) error {
				return g.Reset(ctx, "repo", &GitResetOptions{Mode: "mixed", Target: "HEAD~1", Files: []string{"relative"}})
			},
		},
		{
			"restore defaults", "POST", "/git/restore", `{"path":"repo","files":["relative"]}`,
			func(ctx context.Context, g *Git) error { return g.Restore(ctx, "repo", []string{"relative"}, nil) },
		},
		{
			"restore", "POST", "/git/restore", `{"path":"repo","files":["relative"],"staged":true,"worktree":false,"source":"HEAD"}`,
			func(ctx context.Context, g *Git) error {
				return g.Restore(ctx, "repo", []string{"relative"}, &GitRestoreOptions{Staged: &yes, Worktree: &no, Source: "HEAD"})
			},
		},
		{
			"create branch", "POST", "/git/branches", `{"path":"repo","name":"+feature"}`,
			func(ctx context.Context, g *Git) error { return g.CreateBranch(ctx, "repo", "+feature") },
		},
		{
			"delete default", "DELETE", "/git/branches", `{"path":"repo","name":"feature"}`,
			func(ctx context.Context, g *Git) error { return g.DeleteBranch(ctx, "repo", "feature", nil) },
		},
		{
			"delete force", "DELETE", "/git/branches", `{"path":"repo","name":"feature","force":true}`,
			func(ctx context.Context, g *Git) error {
				return g.DeleteBranch(ctx, "repo", "feature", &GitDeleteBranchOptions{Force: true})
			},
		},
		{
			"checkout", "POST", "/git/checkout", `{"path":"repo","branch":"feature"}`,
			func(ctx context.Context, g *Git) error { return g.CheckoutBranch(ctx, "repo", "feature") },
		},
		{
			"add remote", "POST", "/git/remotes", `{"path":"repo","name":"upstream","url":"https://example.org/repo"}`,
			func(ctx context.Context, g *Git) error {
				return g.AddRemote(ctx, "repo", "upstream", "https://example.org/repo")
			},
		},
		{
			"set config", "POST", "/git/config", `{"key":"user.name","value":"","scope":"global"}`,
			func(ctx context.Context, g *Git) error { return g.SetConfig(ctx, "user.name", "", nil) },
		},
		{
			"local config", "POST", "/git/config", `{"key":"user.name","value":"dev","scope":"local","path":"repo"}`,
			func(ctx context.Context, g *Git) error {
				return g.SetConfig(ctx, "user.name", "dev", &GitConfigOptions{Scope: GitConfigScopeLocal, Path: "repo"})
			},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			git, fake := gitTestService(t, map[string]http.HandlerFunc{
				test.method + " /toolbox/sbx" + test.path: jsonHandler(t, struct{}{}),
			})
			// When
			err := test.call(t.Context(), git)
			// Then
			require.NoError(t, err)
			requests := fake.requests()
			require.Len(t, requests, 1)
			require.JSONEq(t, test.body, string(requests[0].Body))
			require.Equal(t, "Bearer caller", requests[0].Header.Get("Authorization"))
		})
	}
}

func TestGitCommit_whenIdentityIsValid(t *testing.T) {
	for _, allowEmpty := range []bool{false, true} {
		t.Run(map[bool]string{false: "ordinary", true: "empty"}[allowEmpty], func(t *testing.T) {
			// Given
			git, fake := gitTestService(t, map[string]http.HandlerFunc{
				"POST /toolbox/sbx/git/commit": jsonHandler(t, map[string]string{"hash": "created-not-head"}),
			})
			// When
			result, err := git.Commit(t.Context(), "/repo", "message", &GitCommitOptions{
				Author: " developer ", Email: " dev@example.org ", AllowEmpty: allowEmpty,
			})
			// Then
			require.NoError(t, err)
			require.Equal(t, "created-not-head", result.Hash)
			body := map[string]json.RawMessage{}
			require.NoError(t, json.Unmarshal(fake.requests()[0].Body, &body))
			require.JSONEq(t, `"developer"`, string(body["author"]))
			require.JSONEq(t, `"dev@example.org"`, string(body["email"]))
			_, presentEmpty := body["allow_empty"]
			require.Equal(t, allowEmpty, presentEmpty)
		})
	}
}
