// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"time"
)

// FileSearchOptions controls Grep and Glob.
type FileSearchOptions struct {
	// Timeout replaces the client's request timeout for this call as its total
	// deadline (work over a large tree can outlast it); zero keeps the request
	// timeout, NoTimeout (or a duration above 2,147,483.647 seconds) disables
	// it, and any other negative value returns ErrBadRequest before a request.
	// The context always bounds the call. A timeout ends the call, not the
	// work in the sandbox.
	Timeout time.Duration
}

// ReplaceOptions controls Replace.
type ReplaceOptions struct {
	// Timeout replaces the client's request timeout for this call as its total
	// deadline (work over a large tree can outlast it); zero keeps the request
	// timeout, NoTimeout (or a duration above 2,147,483.647 seconds) disables
	// it, and any other negative value returns ErrBadRequest before a request.
	// The context always bounds the call. A timeout ends the call, not the
	// work in the sandbox.
	Timeout time.Duration
}

// checkCallTimeout refuses a negative per-call timeout other than NoTimeout.
func checkCallTimeout(timeout time.Duration) error {
	return checkDuration("Timeout", timeout, NoTimeout)
}

// searchTimeout is the per-call timeout of at most one options value.
func searchTimeout(name string, opts []*FileSearchOptions) (time.Duration, error) {
	if len(opts) > 1 {
		return 0, badRequest(name + " takes at most one FileSearchOptions")
	}
	if len(opts) == 0 || opts[0] == nil {
		return 0, nil
	}
	return opts[0].Timeout, checkCallTimeout(opts[0].Timeout)
}

// Grep searches contents for a literal substring, not a regular expression.
// Unreadable descendants are reported in Skipped; an unreadable root returns ErrFileAccessDenied.
// Pass at most one options value.
func (f *FileSystem) Grep(ctx context.Context, path, pattern string, opts ...*FileSearchOptions) (
	*FileSearchResult[FileMatch], error,
) {
	query, err := filePath(path)
	if err != nil {
		return nil, err
	}
	timeout, err := searchTimeout("Grep", opts)
	if err != nil {
		return nil, err
	}
	query.Set("pattern", pattern)
	var raw json.RawMessage
	spec := toolboxRequest{Path: "/files/find", Query: query, Result: &raw, Timeout: timeout}
	if requestErr := f.sandbox.toolbox().do(ctx, spec); requestErr != nil {
		return nil, requestErr
	}
	itemsRaw := raw
	skipped := []string{}
	if !bytes.HasPrefix(bytes.TrimSpace(raw), []byte("[")) {
		object, parseErr := parseObject(raw, "grep")
		if parseErr != nil {
			return nil, parseErr
		}
		itemsRaw = object["items"]
		skipped, err = fileSkipped(object)
		if err != nil {
			return nil, err
		}
	}
	items, err := fileItems(itemsRaw, "items", parseFileMatch)
	if err != nil {
		return nil, err
	}
	return &FileSearchResult[FileMatch]{Items: items, Skipped: skipped}, nil
}

// Glob finds paths whose base names match pattern. It does not search contents.
// Pass at most one options value.
func (f *FileSystem) Glob(ctx context.Context, path, pattern string, opts ...*FileSearchOptions) (
	*FileSearchResult[string], error,
) {
	query, err := filePath(path)
	if err != nil {
		return nil, err
	}
	timeout, err := searchTimeout("Glob", opts)
	if err != nil {
		return nil, err
	}
	query.Set("pattern", pattern)
	var raw json.RawMessage
	spec := toolboxRequest{Path: "/files/search", Query: query, Result: &raw, Timeout: timeout}
	if requestErr := f.sandbox.toolbox().do(ctx, spec); requestErr != nil {
		return nil, requestErr
	}
	object, err := parseObject(raw, "glob")
	if err != nil {
		return nil, err
	}
	items, err := fileStrings(object["files"], "files")
	if err != nil {
		return nil, err
	}
	skipped, err := fileSkipped(object)
	if err != nil {
		return nil, err
	}
	return &FileSearchResult[string]{Items: items, Skipped: skipped}, nil
}

func fileSkipped(object jsonObject) ([]string, error) {
	if !present(object["skipped"]) {
		return []string{}, nil
	}
	return fileStrings(object["skipped"], "skipped")
}

// Replace substitutes a literal substring in each requested file.
// Per-file failures are returned as FileReplaceResult values.
// Pass at most one options value.
func (f *FileSystem) Replace(ctx context.Context, files []string, pattern, replacement string,
	opts ...*ReplaceOptions,
) (*Page[FileReplaceResult], error) {
	paths, err := filePaths(files)
	if err != nil {
		return nil, err
	}
	if len(opts) > 1 {
		return nil, badRequest("Replace takes at most one ReplaceOptions")
	}
	var timeout time.Duration
	if len(opts) == 1 && opts[0] != nil {
		timeout = opts[0].Timeout
		if timeoutErr := checkCallTimeout(timeout); timeoutErr != nil {
			return nil, timeoutErr
		}
	}
	body := struct {
		Files    []string `json:"files"`
		Pattern  string   `json:"pattern"`
		NewValue string   `json:"newValue"`
	}{paths, pattern, replacement}
	var raw json.RawMessage
	if requestErr := f.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/files/replace", Body: body, Result: &raw, Timeout: timeout,
	}); requestErr != nil {
		return nil, requestErr
	}
	return filePage(raw, parseFileReplace)
}
