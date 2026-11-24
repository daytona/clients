// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// FileInfo describes a sandbox file or directory.
type FileInfo struct {
	// Name is the entry name.
	Name string
	// Path is the absolute sandbox path.
	Path string
	// Size is the file size in bytes.
	Size int64
	// IsDir distinguishes directories.
	IsDir bool
	// Mode is the server's mode string.
	Mode string
	// Permissions is the server's permission string.
	Permissions string
	// Owner is the owning user.
	Owner string
	// Group is the owning group.
	Group string
	// ModifiedAt is the modification timestamp.
	ModifiedAt time.Time
	// ModTime is the server's legacy modification-time representation.
	ModTime string
}

// FileMatch is a literal content-search match.
type FileMatch struct {
	// File is the matched path.
	File string
	// Line is the one-based line number reported by the sandbox.
	Line int64
	// Content is the matched line.
	Content string
}

// FileSearchResult contains matches and unreadable descendants (at most 1000).
// An unreadable search root returns ErrFileAccessDenied instead.
type FileSearchResult[T any] struct {
	// Items contains the matches.
	Items []T
	// Skipped contains paths the sandbox user could not read.
	Skipped []string
}

// FileReplaceResult reports a replacement's outcome for one file.
type FileReplaceResult struct {
	// File is the affected path.
	File string
	// Success reports whether replacement succeeded.
	Success bool
	// Error is the optional server explanation; nil means absent.
	Error *string
}

// BulkWriteFile describes a successfully uploaded file.
type BulkWriteFile struct {
	// Path is the destination path.
	Path string
	// Bytes is the number of bytes written.
	Bytes int64
}

// BulkWriteResult reports successes and per-file failures of WriteMany.
type BulkWriteResult struct {
	// Files contains successful uploads.
	Files []BulkWriteFile
	// Errors contains server explanations of failed uploads.
	Errors []string
}

// BulkReadError describes a per-file failure without failing the whole ReadMany.
type BulkReadError struct {
	// Path is the requested path.
	Path string
	// Message explains the failure.
	Message string
	// Code is the optional machine-readable code.
	Code string
	// Source identifies the optional error producer.
	Source string
}

// BulkReadResult contains downloaded bytes and per-file failures.
type BulkReadResult struct {
	// Files maps full server-returned paths to exact file bytes.
	Files map[string][]byte
	// Errors contains files that could not be read.
	Errors []BulkReadError
}
