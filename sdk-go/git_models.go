// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// GitStatus describes branch tracking and working tree changes.
type GitStatus struct {
	// CurrentBranch is the checked-out branch.
	CurrentBranch string
	// Ahead counts commits ahead of upstream.
	Ahead int64
	// Behind counts commits behind upstream.
	Behind int64
	// BranchPublished reports whether the branch has an upstream.
	BranchPublished bool
	// FileStatus contains the repository's changed files.
	FileStatus []GitFileStatus
}

// GitFileStatus describes one path in the index and working tree.
type GitFileStatus struct {
	// Name is the file path.
	Name string
	// Staging is Git's index status code.
	Staging string
	// Worktree is Git's working tree status code.
	Worktree string
	// Extra contains the additional path information reported by Git.
	Extra string
}

// GitCommitResponse identifies the commit created by Commit.
type GitCommitResponse struct {
	// Hash is the created commit's full SHA.
	Hash string
}

// GitCommit is a commit history entry.
type GitCommit struct {
	// Hash is the full commit SHA.
	Hash string
	// Message is the commit message.
	Message string
	// Author is the author name.
	Author string
	// Email is the author email.
	Email string
	// Timestamp is the author timestamp.
	Timestamp time.Time
}

// GitRemote is a configured repository remote.
type GitRemote struct {
	// Name identifies the remote.
	Name string
	// URL is the remote URL, with credentials removed by the server.
	URL string
}
