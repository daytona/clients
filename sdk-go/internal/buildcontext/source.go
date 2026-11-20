// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"context"
	"errors"
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
	initial, err := os.Stat(s.LocalPath)
	if err != nil {
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
	if !os.SameFile(initial, info) {
		return "", nil, invalid("local source changed while resolving: "+quote(s.LocalPath), nil)
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

func (s Source) entries(ctx context.Context) (result *entrySet, err error) {
	resolved, info, err := s.resolve()
	if err != nil {
		return nil, err
	}
	if s.directory {
		if !info.IsDir() {
			return nil, invalid("local source is not a directory: "+quote(s.LocalPath), nil)
		}
		root, rootErr := openDirectory(resolved, info)
		if rootErr != nil {
			return nil, rootErr
		}
		children, walkErr := walkDirectory(ctx, root, "")
		if walkErr != nil {
			return nil, walkErr
		}
		items := []entry{{name: s.ArchivePath + "/", kind: dirEntry, mode: permissionBits(info.Mode())}}
		for _, child := range children.items {
			child.name = s.ArchivePath + "/" + child.name
			items = append(items, child)
		}
		children.items = items
		return children, nil
	}
	if !info.Mode().IsRegular() {
		return nil, invalid("local source is not a regular file: "+quote(s.LocalPath), nil)
	}
	root, err := os.OpenRoot(filepath.Dir(resolved))
	if err != nil {
		return nil, invalid("local source cannot be opened: "+quote(s.LocalPath), err)
	}
	defer func() {
		if err != nil {
			err = errors.Join(err, root.Close())
		}
	}()
	item, err := collectEntry(root, filepath.Base(resolved))
	if err != nil {
		return nil, err
	}
	if item.kind != fileEntry || !os.SameFile(info, item.info) {
		return nil, invalid("local source changed: "+quote(s.LocalPath), nil)
	}
	item.name = s.ArchivePath
	return &entrySet{root: root, items: []entry{
		{name: path.Dir(s.ArchivePath) + "/", kind: dirEntry, mode: 0o755},
		item,
	}}, nil
}
