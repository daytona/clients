// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"encoding/json"
	"errors"
)

func parseFileInfo(raw json.RawMessage) (FileInfo, error) {
	object, err := parseObject(raw, "file info")
	if err != nil {
		return FileInfo{}, err
	}
	var info FileInfo
	for _, entry := range []struct {
		name   string
		target *string
	}{
		{"name", &info.Name},
		{"path", &info.Path},
		{"mode", &info.Mode},
		{"permissions", &info.Permissions},
		{"owner", &info.Owner},
		{"group", &info.Group},
		{"modTime", &info.ModTime},
	} {
		value, fieldErr := field[string](object, entry.name, true)
		if fieldErr != nil {
			return FileInfo{}, fieldErr
		}
		*entry.target = value
	}
	size, sizeErr := fileNonnegative(object, "size")
	isDir, dirErr := field[bool](object, "isDir", true)
	_, requiredErr := field[string](object, "modifiedAt", true)
	modified, timeErr := timestampField(object, "modifiedAt")
	if parseErr := errors.Join(sizeErr, dirErr, requiredErr, timeErr); parseErr != nil {
		return FileInfo{}, parseErr
	}
	info.Size, info.IsDir, info.ModifiedAt = size, isDir, modified
	return info, nil
}

func fileNonnegative(object jsonObject, name string) (int64, error) {
	value, err := integerField(object, name, true)
	if err != nil {
		return 0, err
	}
	if value < 0 {
		return 0, badRequest("response field " + name + " must be nonnegative")
	}
	return value, nil
}

func fileStrings(raw json.RawMessage) ([]string, error) {
	return fileItems(raw, func(item json.RawMessage) (string, error) {
		return field[string](jsonObject{"item": item}, "item", true)
	})
}

func fileItems[T any](raw json.RawMessage, parse func(json.RawMessage) (T, error)) ([]T, error) {
	var items []json.RawMessage
	if err := json.Unmarshal(raw, &items); err != nil || items == nil {
		return nil, badRequest("expected a response list")
	}
	result := make([]T, len(items))
	for i, item := range items {
		value, err := parse(item)
		if err != nil {
			return nil, err
		}
		result[i] = value
	}
	return result, nil
}

func filePage[T any](raw json.RawMessage, parse func(json.RawMessage) (T, error)) (*Page[T], error) {
	itemsRaw := bytes.TrimSpace(raw)
	var cursor string
	if !bytes.HasPrefix(itemsRaw, []byte("[")) {
		object, err := parseObject(raw, "file list")
		if err != nil {
			return nil, err
		}
		cursor, err = field[string](object, "nextCursor", false)
		if err != nil {
			return nil, err
		}
		itemsRaw = object["items"]
	}
	items, err := fileItems(itemsRaw, parse)
	if err != nil {
		return nil, err
	}
	return &Page[T]{Items: items, NextCursor: cursor}, nil
}

func parseFileMatch(raw json.RawMessage) (FileMatch, error) {
	object, err := parseObject(raw, "file match")
	if err != nil {
		return FileMatch{}, err
	}
	file, fileErr := field[string](object, "file", true)
	line, lineErr := fileNonnegative(object, "line")
	content, contentErr := field[string](object, "content", true)
	return FileMatch{File: file, Line: line, Content: content}, errors.Join(fileErr, lineErr, contentErr)
}

func parseFileReplace(raw json.RawMessage) (FileReplaceResult, error) {
	object, err := parseObject(raw, "file replacement")
	if err != nil {
		return FileReplaceResult{}, err
	}
	file, fileErr := field[string](object, "file", true)
	success, successErr := field[bool](object, "success", true)
	message, messageErr := field[*string](object, "error", false)
	return FileReplaceResult{File: file, Success: success, Error: message}, errors.Join(fileErr, successErr, messageErr)
}

func parseBulkWrite(raw json.RawMessage) (*BulkWriteResult, error) {
	object, err := parseObject(raw, "bulk upload")
	if err != nil {
		return nil, err
	}
	files, filesErr := fileItems(object["files"], func(item json.RawMessage) (BulkWriteFile, error) {
		entry, entryErr := parseObject(item, "bulk upload file")
		if entryErr != nil {
			return BulkWriteFile{}, entryErr
		}
		path, pathErr := field[string](entry, "path", true)
		size, sizeErr := fileNonnegative(entry, "bytes")
		return BulkWriteFile{Path: path, Bytes: size}, errors.Join(pathErr, sizeErr)
	})
	messages, messagesErr := fileStrings(object["errors"])
	if parseErr := errors.Join(filesErr, messagesErr); parseErr != nil {
		return nil, parseErr
	}
	return &BulkWriteResult{Files: files, Errors: messages}, nil
}
