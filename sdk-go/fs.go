// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// FileSystem accesses a sandbox's files. Obtain it from Sandbox.FS.
// All remote paths must start with "/" and are sent without normalization.
// Handles are safe for concurrent use; returned data belongs to the caller.
type FileSystem struct{ sandbox *Sandbox }

// FS returns the sandbox filesystem service without making a request.
func (s *Sandbox) FS() *FileSystem { return &FileSystem{sandbox: s} }

// ListFilesOptions controls directory traversal.
type ListFilesOptions struct {
	// Depth is the number of levels; zero uses the server default (1).
	Depth int
}

// MkdirOptions configures a new directory.
type MkdirOptions struct {
	// Mode is an octal permission string; empty means "0755".
	Mode string
}

// RemoveFileOptions controls directory removal.
type RemoveFileOptions struct {
	// Recursive removes directory contents too; default false.
	Recursive bool
	// Timeout replaces the client's request timeout for this call as its total
	// deadline (work over a large tree can outlast it); zero keeps the request
	// timeout, NoTimeout (or a duration above 2,147,483.647 seconds) disables
	// it, and any other negative value returns ErrBadRequest before a request.
	// The context always bounds the call. A timeout ends the call, not the
	// work in the sandbox.
	Timeout time.Duration
}

// MoveOptions controls a move.
type MoveOptions struct {
	// Timeout replaces the client's request timeout for this move as its total
	// deadline; zero keeps the request timeout, NoTimeout (or a duration above
	// 2,147,483.647 seconds) disables it. The context always bounds the move.
	Timeout time.Duration
}

// FilePermissionsOptions selects permissions or ownership to change.
type FilePermissionsOptions struct {
	// Mode is an octal permission string; empty leaves it unchanged.
	Mode string
	// Owner is a user name or ID; empty leaves it unchanged.
	Owner string
	// Group is a group name or ID; empty leaves it unchanged.
	Group string
	// Timeout replaces the client's request timeout for this call as its total
	// deadline (a slow sandbox file system can outlast it); zero keeps the request
	// timeout, NoTimeout (or a duration above 2,147,483.647 seconds) disables
	// it, and any other negative value returns ErrBadRequest before a request.
	// The context always bounds the call. A timeout ends the call, not the
	// work in the sandbox.
	Timeout time.Duration
}

func filePath(path string) (url.Values, error) {
	if !strings.HasPrefix(path, "/") {
		return nil, badRequest("path must be absolute (starting with '/')")
	}
	return url.Values{"path": {path}}, nil
}

func filePaths(paths []string) ([]string, error) {
	result := make([]string, len(paths))
	for i, path := range paths {
		if _, err := filePath(path); err != nil {
			return nil, err
		}
		result[i] = path
	}
	return result, nil
}

// List returns one directory listing, accepting legacy arrays and page envelopes.
// There is no cursor input on the server's directory endpoint, so the page
// holds the whole listing and its NextCursor cannot be followed.
func (f *FileSystem) List(ctx context.Context, path string, opts *ListFilesOptions) (*Page[FileInfo], error) {
	query, err := filePath(path)
	if err != nil {
		return nil, err
	}
	if opts != nil && opts.Depth != 0 {
		query.Set("depth", strconv.Itoa(opts.Depth))
	}
	var raw json.RawMessage
	spec := toolboxRequest{Path: "/files", Query: query, Result: &raw}
	if requestErr := f.sandbox.toolbox().do(ctx, spec); requestErr != nil {
		return nil, requestErr
	}
	return filePage(raw, parseFileInfo)
}

// Stat returns file metadata, or ErrFileNotFound when the path does not exist.
func (f *FileSystem) Stat(ctx context.Context, path string) (*FileInfo, error) {
	query, err := filePath(path)
	if err != nil {
		return nil, err
	}
	var raw json.RawMessage
	spec := toolboxRequest{Path: "/files/info", Query: query, Result: &raw}
	if requestErr := f.sandbox.toolbox().do(ctx, spec); requestErr != nil {
		return nil, requestErr
	}
	info, err := parseFileInfo(raw)
	if err != nil {
		return nil, err
	}
	return &info, nil
}

// Mkdir creates a directory, using mode 0755 by default.
func (f *FileSystem) Mkdir(ctx context.Context, path string, opts *MkdirOptions) error {
	query, err := filePath(path)
	if err != nil {
		return err
	}
	mode := "0755"
	if opts != nil && opts.Mode != "" {
		mode = opts.Mode
	}
	query.Set("mode", mode)
	return f.sandbox.toolbox().do(ctx, toolboxRequest{Method: http.MethodPost, Path: "/files/folder", Query: query})
}

// Remove deletes a file, or recursively deletes a directory when requested.
func (f *FileSystem) Remove(ctx context.Context, path string, opts *RemoveFileOptions) error {
	query, err := filePath(path)
	if err != nil {
		return err
	}
	var timeout time.Duration
	if opts != nil {
		query.Set("recursive", strconv.FormatBool(opts.Recursive))
		timeout = opts.Timeout
	}
	if timeoutErr := checkCallTimeout(timeout); timeoutErr != nil {
		return timeoutErr
	}
	return f.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodDelete, Path: "/files", Query: query, Timeout: timeout,
	})
}

// Move moves or renames a file between absolute sandbox paths.
//
// A move to another file system is a copy in the sandbox and can take longer
// than the client's request timeout; MoveOptions.Timeout replaces it for this
// call. Pass at most one options value. The SDK does not retry a move that
// timed out or failed (only a 401, which the sandbox refuses before moving
// anything, is re-sent once with a fresh token). A timeout or cancellation
// ends the call, not the move: the move may still be running in the sandbox,
// or have finished, so check the destination (for example with Stat) before
// moving again.
func (f *FileSystem) Move(ctx context.Context, source, destination string, opts ...*MoveOptions) error {
	if _, err := filePaths([]string{source, destination}); err != nil {
		return err
	}
	if len(opts) > 1 {
		return badRequest("Move takes at most one MoveOptions")
	}
	var timeout time.Duration
	if len(opts) == 1 && opts[0] != nil {
		timeout = opts[0].Timeout
		if err := checkDuration("Timeout", timeout, NoTimeout); err != nil {
			return err
		}
	}
	return f.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/files/move",
		Query:   url.Values{"source": {source}, "destination": {destination}},
		Timeout: timeout,
	})
}

// Chmod changes permissions and ownership. Nil opts leaves all fields unspecified.
func (f *FileSystem) Chmod(ctx context.Context, path string, opts *FilePermissionsOptions) error {
	query, err := filePath(path)
	if err != nil {
		return err
	}
	var timeout time.Duration
	if opts != nil {
		for name, value := range map[string]string{"mode": opts.Mode, "owner": opts.Owner, "group": opts.Group} {
			if value != "" {
				query.Set(name, value)
			}
		}
		timeout = opts.Timeout
	}
	if timeoutErr := checkCallTimeout(timeout); timeoutErr != nil {
		return timeoutErr
	}
	return f.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/files/permissions", Query: query, Timeout: timeout,
	})
}
