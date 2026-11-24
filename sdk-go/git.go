// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strings"

	wire "go.daytona.com/sdk/v2/internal/gen/toolbox"
)

// Git provides sandbox repository operations. Remote paths and pathspecs are
// passed to Git unchanged, including relative paths. Obtain it with Sandbox.Git.
// Server failures retain codes such as ErrGitAuthFailed, ErrGitRepoNotFound,
// ErrGitBranchNotFound and ErrGitBranchExists.
type Git struct{ sandbox *Sandbox }

// Git returns this sandbox's concurrency-safe Git service.
func (s *Sandbox) Git() *Git { return &Git{sandbox: s} }

// GitInitOptions configures a new repository.
type GitInitOptions struct {
	// Bare creates a bare repository; default false.
	Bare bool
	// InitialBranch selects the initial branch; empty uses Git's default.
	InitialBranch string
}

// GitCommitOptions supplies the required identity for this commit only.
type GitCommitOptions struct {
	// Author is the required non-blank author name.
	Author string
	// Email is the required non-blank author email.
	Email string
	// AllowEmpty permits a commit without staged changes; default false.
	AllowEmpty bool
}

// GitResetOptions selects the reset operation.
type GitResetOptions struct {
	// Mode selects mixed, soft, hard, merge or keep; empty uses Git's default.
	Mode GitResetMode
	// Target is the commit-ish; empty uses Git's default.
	Target string
	// Files contains relative pathspecs; nil omits the restriction.
	Files []string
}

// GitRestoreOptions selects the source and destinations of a restore.
type GitRestoreOptions struct {
	// Staged restores the index when true; nil leaves Git's default.
	Staged *bool
	// Worktree restores working files; nil leaves Git's default.
	Worktree *bool
	// Source is an optional source tree-ish.
	Source string
}

// Init initializes a repository; nil opts uses Git's defaults.
func (g *Git) Init(ctx context.Context, path string, opts *GitInitOptions) error {
	options := GitInitOptions{}
	if opts != nil {
		options = *opts
	}
	return g.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/git/init",
		Body: wire.InitRequest{Path: &path, Bare: &options.Bare, InitialBranch: gitOptional(options.InitialBranch)},
	})
}

// Add stages string pathspecs. Nil files is sent as an empty array, never null.
func (g *Git) Add(ctx context.Context, path string, files []string) error {
	paths := append([]string{}, files...)
	return g.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/git/add", Body: wire.AddRequest{Path: &path, Files: &paths},
	})
}

// Reset resets repository state; nil opts uses a mixed reset to HEAD.
func (g *Git) Reset(ctx context.Context, path string, opts *GitResetOptions) error {
	body := wire.ResetRequest{Path: &path}
	if opts != nil {
		body.Mode, body.Target = gitOptional(string(opts.Mode)), gitOptional(opts.Target)
		if opts.Files != nil {
			paths := append([]string{}, opts.Files...)
			body.Files = &paths
		}
	}
	return g.sandbox.toolbox().do(ctx, toolboxRequest{Method: http.MethodPost, Path: "/git/reset", Body: body})
}

// Restore restores pathspecs from the index or Source. Nil opts uses Git's defaults.
func (g *Git) Restore(ctx context.Context, path string, files []string, opts *GitRestoreOptions) error {
	paths := append([]string{}, files...)
	body := wire.RestoreRequest{Path: &path, Files: &paths}
	if opts != nil {
		body.Staged, body.Worktree, body.Source = opts.Staged, opts.Worktree, gitOptional(opts.Source)
	}
	return g.sandbox.toolbox().do(ctx, toolboxRequest{Method: http.MethodPost, Path: "/git/restore", Body: body})
}

// Commit creates a commit and returns its captured hash (not a subsequent HEAD
// lookup). Author and Email are required; invalid input returns ErrBadRequest.
func (g *Git) Commit(ctx context.Context, path, message string, opts *GitCommitOptions) (*GitCommitResponse, error) {
	if opts == nil || strings.TrimSpace(opts.Author) == "" || strings.TrimSpace(opts.Email) == "" {
		return nil, badRequest("git.Commit: Author and Email must be non-blank")
	}
	author, email := strings.TrimSpace(opts.Author), strings.TrimSpace(opts.Email)
	body := wire.CommitRequest{Path: &path, Message: &message, Author: &author, Email: &email}
	if opts.AllowEmpty {
		body.AllowEmpty = &opts.AllowEmpty
	}
	object, err := g.object(ctx, toolboxRequest{Method: http.MethodPost, Path: "/git/commit", Body: body})
	if err != nil {
		return nil, err
	}
	hash, err := field[string](object, "hash", true)
	if err != nil {
		return nil, err
	}
	return &GitCommitResponse{Hash: hash}, nil
}

func gitOptional(value string) *string {
	if value == "" {
		return nil
	}
	return &value
}
