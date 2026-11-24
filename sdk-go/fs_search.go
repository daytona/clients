// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
)

// Grep searches contents for a literal substring, not a regular expression.
// Unreadable descendants are reported in Skipped; an unreadable root returns ErrFileAccessDenied.
func (f *FileSystem) Grep(ctx context.Context, path, pattern string) (*FileSearchResult[FileMatch], error) {
	query, err := filePath(path)
	if err != nil {
		return nil, err
	}
	query.Set("pattern", pattern)
	var raw json.RawMessage
	spec := toolboxRequest{Path: "/files/find", Query: query, Result: &raw}
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
// The walk is recursive and matches every entry, directories and path itself
// included; pattern supports only * and ?.
func (f *FileSystem) Glob(ctx context.Context, path, pattern string) (*FileSearchResult[string], error) {
	query, err := filePath(path)
	if err != nil {
		return nil, err
	}
	query.Set("pattern", pattern)
	var raw json.RawMessage
	spec := toolboxRequest{Path: "/files/search", Query: query, Result: &raw}
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
func (f *FileSystem) Replace(ctx context.Context, files []string, pattern, replacement string) (
	*Page[FileReplaceResult], error,
) {
	paths, err := filePaths(files)
	if err != nil {
		return nil, err
	}
	body := struct {
		Files    []string `json:"files"`
		Pattern  string   `json:"pattern"`
		NewValue string   `json:"newValue"`
	}{paths, pattern, replacement}
	var raw json.RawMessage
	if requestErr := f.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/files/replace", Body: body, Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	return filePage(raw, parseFileReplace)
}
