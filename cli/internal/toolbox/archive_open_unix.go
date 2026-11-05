// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build !windows

package toolbox

import (
	"archive/tar"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"golang.org/x/sys/unix"
)

type unixArchiveExtractor struct {
	rootPath string
	root     *os.File
}

func newArchiveExtractor(dst string) (archiveExtractor, error) {
	rootPath, err := filepath.Abs(dst)
	if err != nil {
		return nil, fmt.Errorf("resolve destination %s: %w", dst, err)
	}
	// Pin the destination before resolving names. Never reopen a resolved path:
	// a concurrent rename or symlink swap must not redirect descriptor writes.
	fd, err := unix.Open(rootPath, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", rootPath, err)
	}
	root := os.NewFile(uintptr(fd), rootPath)
	// Keep parent paths and containment checks in the same canonical space.
	rootPath, err = filepath.EvalSymlinks(rootPath)
	if err != nil {
		_ = root.Close()
		return nil, fmt.Errorf("resolve destination %s: %w", dst, err)
	}
	opened, err := root.Stat()
	if err != nil {
		_ = root.Close()
		return nil, fmt.Errorf("stat destination %s: %w", dst, err)
	}
	resolved, err := os.Stat(rootPath)
	if err != nil {
		_ = root.Close()
		return nil, fmt.Errorf("stat destination %s: %w", dst, err)
	}
	if !os.SameFile(opened, resolved) {
		_ = root.Close()
		return nil, fmt.Errorf("destination %s changed while opening the archive root", dst)
	}
	return &unixArchiveExtractor{rootPath: rootPath, root: root}, nil
}

func (e *unixArchiveExtractor) Close() error {
	return e.root.Close()
}

func (e *unixArchiveExtractor) ExtractDirectory(relPath string, target string, mode os.FileMode) error {
	dir, err := e.ensureDir(relPath)
	if err != nil {
		return err
	}
	defer dir.Close()
	if err := dir.Chmod(mode); err != nil {
		return fmt.Errorf("chmod %s: %w", target, err)
	}
	return nil
}

func (e *unixArchiveExtractor) OpenRegular(relPath string, target string, mode os.FileMode) (*os.File, error) {
	parent, base, _, err := e.openParent(relPath, true)
	if err != nil {
		return nil, err
	}
	defer parent.Close()
	if archiveAfterParentPinned != nil {
		archiveAfterParentPinned(target)
	}
	return openRegularAt(parent, base, target, mode)
}

func (e *unixArchiveExtractor) ExtractHardLink(header *tar.Header, relPath string, target string) error {
	if filepath.IsAbs(header.Linkname) || hasParentPathComponent(header.Linkname) {
		return fmt.Errorf("tar hard link %s -> %s: absolute or parent-relative link targets are refused", header.Name, header.Linkname)
	}
	source, err := resolveArchiveTarget(e.rootPath, header.Linkname)
	if err != nil {
		return fmt.Errorf("tar hard link %s -> %s: %w", header.Name, header.Linkname, err)
	}
	sourceParent, sourceBase, _, err := e.openParent(source.relPath, false)
	if err != nil {
		return fmt.Errorf("tar hard link %s -> %s: %w", header.Name, header.Linkname, err)
	}
	defer sourceParent.Close()
	if err := requireRegularAt(sourceParent, sourceBase, source.target); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return fmt.Errorf("tar hard link %s -> %s: target has not been extracted yet", header.Name, header.Linkname)
		}
		return fmt.Errorf("tar hard link %s -> %s: %w", header.Name, header.Linkname, err)
	}
	destParent, destBase, _, err := e.openParent(relPath, true)
	if err != nil {
		return err
	}
	defer destParent.Close()
	if err := unlinkIfExists(destParent, destBase); err != nil {
		return err
	}
	if err := unix.Linkat(int(sourceParent.Fd()), sourceBase, int(destParent.Fd()), destBase, 0); err == nil {
		return nil
	}
	return copyHardLinkTargetAt(sourceParent, sourceBase, destParent, destBase, source.target, target, os.FileMode(header.Mode))
}

func (e *unixArchiveExtractor) ExtractSymlink(header *tar.Header, relPath string, target string) error {
	if filepath.IsAbs(header.Linkname) || hasParentPathComponent(header.Linkname) {
		return fmt.Errorf("tar symlink %s -> %s: absolute or parent-relative link targets are refused", header.Name, header.Linkname)
	}
	parent, base, parentPath, err := e.openParent(relPath, true)
	if err != nil {
		return err
	}
	defer parent.Close()
	if !within(e.rootPath, parentPath) {
		return fmt.Errorf("tar symlink %s: parent escapes destination", header.Name)
	}
	dest := filepath.Clean(filepath.Join(parentPath, header.Linkname))
	resolvedDest, err := evalSymlinksLenient(dest)
	if err != nil {
		return fmt.Errorf("tar symlink %s -> %s: %w", header.Name, header.Linkname, err)
	}
	if !within(e.rootPath, resolvedDest) {
		return fmt.Errorf("tar symlink %s -> %s escapes destination", header.Name, header.Linkname)
	}
	safeLink, err := filepath.Rel(parentPath, resolvedDest)
	if err != nil {
		return fmt.Errorf("tar symlink %s: %w", header.Name, err)
	}
	if err := unlinkIfExists(parent, base); err != nil {
		return err
	}
	if err := unix.Symlinkat(safeLink, int(parent.Fd()), base); err != nil {
		return fmt.Errorf("create symlink %s: %w", target, err)
	}
	return nil
}

func (e *unixArchiveExtractor) ensureDir(relPath string) (*os.File, error) {
	current, err := duplicateDir(e.root)
	if err != nil {
		return nil, err
	}
	currentPath := e.rootPath
	for _, part := range splitArchiveRel(relPath) {
		currentPath = filepath.Join(currentPath, part)
		next, err := openDirectoryAt(current, part, currentPath, true)
		if err != nil {
			_ = current.Close()
			return nil, err
		}
		_ = current.Close()
		current = next
	}
	return current, nil
}

func (e *unixArchiveExtractor) openParent(relPath string, create bool) (*os.File, string, string, error) {
	parts := splitArchiveRel(relPath)
	if len(parts) == 0 {
		root, err := duplicateDir(e.root)
		return root, ".", e.rootPath, err
	}
	current, err := duplicateDir(e.root)
	if err != nil {
		return nil, "", "", err
	}
	currentPath := e.rootPath
	for _, part := range parts[:len(parts)-1] {
		currentPath = filepath.Join(currentPath, part)
		next, openErr := openDirectoryAt(current, part, currentPath, create)
		if openErr != nil {
			_ = current.Close()
			return nil, "", "", openErr
		}
		_ = current.Close()
		current = next
	}
	return current, parts[len(parts)-1], currentPath, nil
}
