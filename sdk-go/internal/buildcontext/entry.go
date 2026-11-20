// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"errors"
	"os"
	"path"
	"path/filepath"
	"strings"
	"syscall"
)

type entryKind uint8

const (
	fileEntry entryKind = iota
	bytesEntry
	dirEntry
	symlinkEntry
)

type entry struct {
	name    string
	kind    entryKind
	mode    int64
	local   string
	info    os.FileInfo
	content []byte
	target  string
}

func collectEntry(current, relative string) (entry, error) {
	info, err := os.Lstat(current)
	if err != nil {
		return entry{}, err
	}
	item := entry{name: relative, local: current, info: info, mode: permissionBits(info.Mode())}
	switch {
	case info.Mode().IsRegular():
		item.kind = fileEntry
	case info.IsDir():
		item.kind = dirEntry
		item.name += "/"
	case info.Mode()&os.ModeSymlink != 0:
		item.kind = symlinkEntry
		item.target, err = os.Readlink(current)
		if err != nil {
			return entry{}, err
		}
		if err = checkUTF8(item.target); err != nil {
			return entry{}, err
		}
		inside := path.Clean(path.Join(path.Dir(relative), filepath.ToSlash(item.target)))
		if filepath.IsAbs(item.target) || inside == ".." || strings.HasPrefix(inside, "../") {
			return entry{}, invalid("symbolic link leaves its local source: "+quote(current), nil)
		}
	default:
		return entry{}, invalid("unsupported file type in build context: "+quote(current), nil)
	}
	return item, nil
}

func (e entry) open() (file *os.File, size int64, err error) {
	file, err = openRegular(e.local)
	if err != nil {
		if denied(err) {
			return nil, 0, fileError(e.local, err)
		}
		if missing(err) || errors.Is(err, syscall.ENXIO) {
			return nil, 0, invalid("build context changed: "+quote(e.local), err)
		}
		return nil, 0, err
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
