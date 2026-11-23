// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"

	wire "go.daytona.com/sdk/v2/internal/gen/toolbox"
)

// GitDeleteBranchOptions controls safe versus forced branch deletion.
type GitDeleteBranchOptions struct {
	// Force deletes even an unmerged branch; default false.
	Force bool
}

// ListBranches returns the repository's branch names in a single page.
func (g *Git) ListBranches(ctx context.Context, path string) (*Page[string], error) {
	object, err := g.object(ctx, toolboxRequest{Path: "/git/branches", Query: url.Values{"path": {path}}})
	if err != nil {
		return nil, err
	}
	items, err := field[[]string](object, "branches", true)
	if err != nil {
		return nil, err
	}
	rows, err := field[[]json.RawMessage](object, "branches", true)
	if err != nil {
		return nil, err
	}
	for _, row := range rows {
		if !present(row) {
			return nil, badRequest("git branches entries must be strings")
		}
	}
	return &Page[string]{Items: items}, nil
}

// CreateBranch creates name; ErrGitBranchExists identifies a duplicate.
func (g *Git) CreateBranch(ctx context.Context, path, name string) error {
	return g.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/git/branches", Body: wire.BranchRequest{Path: &path, Name: &name},
	})
}

// DeleteBranch safely deletes a merged branch by default. An unmerged or
// checked-out branch returns ErrConflict (HTTP 409). Force allows deleting an
// unmerged branch, never the checked-out one. Missing names return ErrGitBranchNotFound.
func (g *Git) DeleteBranch(ctx context.Context, path, name string, opts *GitDeleteBranchOptions) error {
	body := wire.BranchRequest{Path: &path, Name: &name}
	if opts != nil && opts.Force {
		body.Force = &opts.Force
	}
	return g.sandbox.toolbox().do(ctx, toolboxRequest{Method: http.MethodDelete, Path: "/git/branches", Body: body})
}

// CheckoutBranch checks out an existing branch; missing branches return ErrGitBranchNotFound.
func (g *Git) CheckoutBranch(ctx context.Context, path, branch string) error {
	return g.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/git/checkout", Body: wire.CheckoutRequest{Path: &path, Branch: &branch},
	})
}

// ListRemotes returns configured remotes. The server strips credentials from URLs.
func (g *Git) ListRemotes(ctx context.Context, path string) (*Page[GitRemote], error) {
	object, err := g.object(ctx, toolboxRequest{Path: "/git/remotes", Query: url.Values{"path": {path}}})
	if err != nil {
		return nil, err
	}
	rows, err := field[[]jsonObject](object, "remotes", true)
	if err != nil {
		return nil, err
	}
	items := make([]GitRemote, 0, len(rows))
	for _, row := range rows {
		values, parseErr := gitStrings(row, []string{"name", "url"})
		if parseErr != nil {
			return nil, parseErr
		}
		items = append(items, GitRemote{Name: values[0], URL: values[1]})
	}
	return &Page[GitRemote]{Items: items}, nil
}

// AddRemote adds a named remote at url.
func (g *Git) AddRemote(ctx context.Context, path, name, url string) error {
	return g.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/git/remotes",
		Body: wire.AddRemoteRequest{Path: &path, Name: &name, Url: &url},
	})
}
