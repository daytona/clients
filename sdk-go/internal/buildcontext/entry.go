// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"errors"
	"os"
	"path"
	"path/filepath"
	"strings"
)

type entryKind uint8

const (
	fileEntry entryKind = iota
	bytesEntry
	dirEntry
	symlinkEntry
)

type entry struct {
	name     string
	kind     entryKind
	mode     int64
	local    string
	root     *os.Root
	relative string
	info     os.FileInfo
	content  []byte
	target   string
}

func collectEntry(root *os.Root, relative string) (entry, error) {
	current := filepath.Join(root.Name(), filepath.FromSlash(relative))
	info, err := root.Lstat(filepath.FromSlash(relative))
	if err != nil {
		return entry{}, invalid("build context changed: "+quote(current), err)
	}
	item := entry{
		name: relative, local: current, root: root, relative: filepath.FromSlash(relative),
		info: info, mode: permissionBits(info.Mode()),
	}
	switch {
	case info.Mode().IsRegular():
		item.kind = fileEntry
	case info.IsDir():
		item.kind = dirEntry
		item.name += "/"
	case info.Mode()&os.ModeSymlink != 0:
		item.kind = symlinkEntry
		item.target, err = symlinkTarget(root, relative)
		if err != nil {
			return entry{}, err
		}
	default:
		return entry{}, invalid("unsupported file type in build context: "+quote(current), nil)
	}
	return item, nil
}

// symlinkTarget reads and validates a link after its type has been collected.
func symlinkTarget(root *os.Root, relative string) (string, error) {
	current := filepath.Join(root.Name(), filepath.FromSlash(relative))
	target, err := root.Readlink(filepath.FromSlash(relative))
	if err != nil {
		return "", invalid("build context changed: "+quote(current), err)
	}
	if err = checkUTF8(target); err != nil {
		return "", err
	}
	inside := path.Clean(path.Join(path.Dir(relative), filepath.ToSlash(target)))
	if filepath.IsAbs(target) || inside == ".." || strings.HasPrefix(inside, "../") {
		return "", invalid("symbolic link leaves its local source: "+quote(current), nil)
	}
	// Preserve internal (including dangling/cyclic or unreadable) links as
	// links. Root.Stat refuses escapes through parents or intermediate links.
	targetPath := filepath.Dir(filepath.FromSlash(relative)) + string(filepath.Separator) + target
	if _, err = root.Stat(targetPath); err != nil && !missing(err) && !denied(err) {
		return "", invalid("symbolic link leaves its local source: "+quote(current), err)
	}
	return target, nil
}

func (e entry) open() (file *os.File, size int64, err error) {
	file, err = openRegular(e.root, e.relative)
	if err != nil {
		if denied(err) {
			return nil, 0, fileError(e.local, err)
		}
		return nil, 0, invalid("build context changed: "+quote(e.local), err)
	}
	defer func() {
		if err != nil {
			err = errors.Join(err, file.Close())
			file = nil
		}
	}()
	opened, err := file.Stat()
	if err != nil {
		return file, 0, err
	}
	if !opened.Mode().IsRegular() || !os.SameFile(opened, e.info) {
		return file, 0, invalid("build context changed: "+quote(e.local), nil)
	}
	return file, opened.Size(), nil
}
