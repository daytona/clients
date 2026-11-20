// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"context"
	"os"
	"path"
	"path/filepath"
	"strconv"
	"strings"
)

// Source is a declaration-time snapshot of a builder input. Construct it with
// NewSource; contents are read only while preparing an archive.
type Source struct {
	// LocalPath is the absolute path, retaining a declared symbolic link.
	LocalPath string
	// ArchivePath is its deterministic positional path in the archive.
	ArchivePath string
	directory   bool
	link        bool
	real        string
}

// NewSource snapshots path metadata without reading file contents. Directory
// selects a directory rather than a regular file; position starts at one.
func NewSource(localPath string, directory bool, position int) (source Source, err error) {
	defer classify(&err)
	if strings.ContainsRune(localPath, 0) {
		return Source{}, invalid("ImageBuilder localPath must not contain a NUL character", nil)
	}
	absolute, err := absolutePath(localPath)
	if err != nil {
		return Source{}, err
	}
	info, err := os.Stat(absolute)
	if err != nil {
		if missing(err) {
			return Source{}, invalid(absolute+" does not exist", err)
		}
		return Source{}, fileError(absolute, err)
	}
	if directory && !info.IsDir() {
		return Source{}, invalid(absolute+" is not a directory", nil)
	}
	if !directory && !info.Mode().IsRegular() {
		return Source{}, invalid(absolute+" is not a file", nil)
	}
	declared, err := os.Lstat(absolute)
	if err != nil {
		return Source{}, err
	}
	resolved, err := filepath.EvalSymlinks(absolute)
	if err != nil {
		return Source{}, err
	}
	archivePath := ".daytona-context/" + strconv.Itoa(position)
	if !directory {
		basename := filepath.Base(absolute)
		if strings.ContainsFunc(basename, unsafeBasename) {
			basename = "file"
		}
		archivePath += "/" + basename
	}
	return Source{
		LocalPath: absolute, ArchivePath: archivePath, directory: directory,
		link: declared.Mode()&os.ModeSymlink != 0, real: resolved,
	}, nil
}

func unsafeBasename(r rune) bool {
	return r < 32 || r >= 0x7f && r <= 0x9f || strings.ContainsRune("$\\*?[]'\"", r)
}

func (s Source) resolve() (string, os.FileInfo, error) {
	if _, err := os.Stat(s.LocalPath); err != nil {
		if missing(err) {
			return "", nil, invalid("local source does not exist: "+quote(s.LocalPath), err)
		}
		return "", nil, fileError(s.LocalPath, err)
	}
	resolved, err := filepath.EvalSymlinks(s.LocalPath)
	if err != nil {
		if missing(err) {
			return "", nil, invalid("local source does not exist: "+quote(s.LocalPath), err)
		}
		return "", nil, fileError(s.LocalPath, err)
	}
	if err = checkUTF8(resolved); err != nil {
		return "", nil, err
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return "", nil, fileError(s.LocalPath, err)
	}
	declared, err := os.Lstat(s.LocalPath)
	if err != nil {
		return "", nil, fileError(s.LocalPath, err)
	}
	if !s.link && (resolved != s.real || declared.Mode()&os.ModeSymlink != 0) {
		return "", nil, invalid("local source changed since it was added: "+quote(s.LocalPath), nil)
	}
	return resolved, info, nil
}

func (s Source) entries(ctx context.Context) ([]entry, error) {
	resolved, info, err := s.resolve()
	if err != nil {
		return nil, err
	}
	if s.directory {
		if !info.IsDir() {
			return nil, invalid("local source is not a directory: "+quote(s.LocalPath), nil)
		}
		children, _, walkErr := directoryEntries(ctx, resolved, "")
		if walkErr != nil {
			return nil, walkErr
		}
		result := []entry{{name: s.ArchivePath + "/", kind: dirEntry, mode: permissionBits(info.Mode())}}
		for _, child := range children {
			child.name = s.ArchivePath + "/" + child.name
			result = append(result, child)
		}
		return result, nil
	}
	if !info.Mode().IsRegular() {
		return nil, invalid("local source is not a regular file: "+quote(s.LocalPath), nil)
	}
	return []entry{
		{name: path.Dir(s.ArchivePath) + "/", kind: dirEntry, mode: 0o755},
		{name: s.ArchivePath, kind: fileEntry, local: resolved, info: info, mode: permissionBits(info.Mode())},
	}, nil
}
