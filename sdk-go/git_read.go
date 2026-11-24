// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/url"
)

// Status returns the current branch, tracking counts and changed files.
func (g *Git) Status(ctx context.Context, path string) (*GitStatus, error) {
	object, err := g.object(ctx, toolboxRequest{Path: "/git/status", Query: url.Values{"path": {path}}})
	if err != nil {
		return nil, err
	}
	branch, err := field[string](object, "currentBranch", true)
	if err != nil {
		return nil, err
	}
	ahead, err := integerField(object, "ahead", true)
	if err != nil {
		return nil, err
	}
	behind, err := integerField(object, "behind", true)
	if err != nil {
		return nil, err
	}
	published, err := field[bool](object, "branchPublished", true)
	if err != nil {
		return nil, err
	}
	rows, err := field[[]jsonObject](object, "fileStatus", true)
	if err != nil {
		return nil, err
	}
	files := make([]GitFileStatus, 0, len(rows))
	for _, row := range rows {
		values, parseErr := gitStrings(row, []string{"name", "staging", "worktree", "extra"})
		if parseErr != nil {
			return nil, parseErr
		}
		files = append(files, GitFileStatus{Name: values[0], Staging: values[1], Worktree: values[2], Extra: values[3]})
	}
	return &GitStatus{
		CurrentBranch: branch, Ahead: ahead, Behind: behind, BranchPublished: published, FileStatus: files,
	}, nil
}

// History returns commits newest first, accepting both array and envelope
// responses. It makes exactly one request, retaining an optional next cursor.
func (g *Git) History(ctx context.Context, path string) (*Page[GitCommit], error) {
	var raw json.RawMessage
	if err := g.sandbox.toolbox().do(ctx, toolboxRequest{
		Path: "/git/history", Query: url.Values{"path": {path}}, Result: &raw,
	}); err != nil {
		return nil, err
	}
	var rows []jsonObject
	var cursor string
	var list []json.RawMessage
	if json.Unmarshal(raw, &list) == nil && list != nil && json.Unmarshal(raw, &rows) != nil {
		return nil, badRequest("git history response items must be objects")
	}
	if decodeErr := json.Unmarshal(raw, &rows); decodeErr != nil || rows == nil {
		object, err := parseObject(raw, "git history")
		if err != nil {
			return nil, err
		}
		rows, err = field[[]jsonObject](object, "items", true)
		if err != nil {
			return nil, err
		}
		cursor, err = field[string](object, "nextCursor", false)
		if err != nil {
			return nil, err
		}
	}
	items := make([]GitCommit, 0, len(rows))
	for _, row := range rows {
		values, parseErr := gitStrings(row, []string{"hash", "message", "author", "email"})
		if parseErr != nil {
			return nil, parseErr
		}
		if _, timestampErr := field[string](row, "timestamp", true); timestampErr != nil {
			return nil, timestampErr
		}
		timestamp, timestampErr := timestampField(row, "timestamp")
		if timestampErr != nil {
			return nil, timestampErr
		}
		items = append(items, GitCommit{
			Hash: values[0], Message: values[1], Author: values[2], Email: values[3], Timestamp: timestamp,
		})
	}
	return &Page[GitCommit]{Items: items, NextCursor: cursor}, nil
}

func (g *Git) object(ctx context.Context, request toolboxRequest) (jsonObject, error) {
	var raw json.RawMessage
	spec := request
	spec.Result = &raw
	if err := g.sandbox.toolbox().do(ctx, spec); err != nil {
		return nil, err
	}
	return parseObject(raw, "git")
}

func gitStrings(object jsonObject, names []string) ([]string, error) {
	values := make([]string, len(names))
	for index, name := range names {
		value, err := field[string](object, name, true)
		if err != nil {
			return nil, err
		}
		values[index] = value
	}
	return values, nil
}
