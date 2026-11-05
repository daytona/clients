// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build windows

package toolbox

import (
	"archive/tar"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
)

// windowsArchiveExtractor creates entries relative to directory handles
// (os.Root: NtCreateFile with a RootDirectory). The destination is pinned
// when extraction starts, and each parent is opened from the handle of its
// own parent, so a concurrent rename or link swap of a directory cannot
// redirect a regular file or directory write. Symlink entries are created in
// the pinned parent too, but their target containment check is name-based
// (EvalSymlinks plus within). Hard links (os.Root.Link) re-resolve both
// relative paths from the pinned destination; os.Root keeps them inside it.
type windowsArchiveExtractor struct {
	rootPath string
	root     *os.Root
}

func newArchiveExtractor(dst string) (archiveExtractor, error) {
	rootPath, err := filepath.Abs(dst)
	if err != nil {
		return nil, fmt.Errorf("resolve destination %s: %w", dst, err)
	}
	root, err := os.OpenRoot(rootPath)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", rootPath, err)
	}
	return &windowsArchiveExtractor{rootPath: rootPath, root: root}, nil
}

func (e *windowsArchiveExtractor) Close() error {
	return e.root.Close()
}

func (e *windowsArchiveExtractor) ExtractDirectory(relPath string, target string, mode os.FileMode) error {
	parent, base, err := e.openParent(relPath, true)
	if err != nil {
		return err
	}
	defer closeUnlessRoot(e.root, parent)
	dir, err := openDirectoryIn(parent, base, target, true)
	if err != nil {
		return err
	}
	_ = dir.Close()
	if err := parent.Chmod(base, mode); err != nil {
		return fmt.Errorf("chmod %s: %w", target, err)
	}
	return nil
}

func (e *windowsArchiveExtractor) OpenRegular(relPath string, target string, mode os.FileMode) (*os.File, error) {
	parent, base, err := e.openParent(relPath, true)
	if err != nil {
		return nil, err
	}
	defer closeUnlessRoot(e.root, parent)
	if archiveAfterParentPinned != nil {
		archiveAfterParentPinned(target)
	}
	return openRegularIn(parent, base, target, mode)
}

func (e *windowsArchiveExtractor) ExtractHardLink(header *tar.Header, relPath string, target string) error {
	if filepath.IsAbs(header.Linkname) || hasParentPathComponent(header.Linkname) {
		return fmt.Errorf("tar hard link %s -> %s: absolute or parent-relative link targets are refused", header.Name, header.Linkname)
	}
	source, err := resolveArchiveTarget(e.rootPath, header.Linkname)
	if err != nil {
		return fmt.Errorf("tar hard link %s -> %s: %w", header.Name, header.Linkname, err)
	}
	sourceParent, sourceBase, err := e.openParent(source.relPath, false)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return fmt.Errorf("tar hard link %s -> %s: target has not been extracted yet", header.Name, header.Linkname)
		}
		return fmt.Errorf("tar hard link %s -> %s: %w", header.Name, header.Linkname, err)
	}
	defer closeUnlessRoot(e.root, sourceParent)
	if err := requireRegularIn(sourceParent, sourceBase, source.target); err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return fmt.Errorf("tar hard link %s -> %s: target has not been extracted yet", header.Name, header.Linkname)
		}
		return fmt.Errorf("tar hard link %s -> %s: %w", header.Name, header.Linkname, err)
	}
	destParent, destBase, err := e.openParent(relPath, true)
	if err != nil {
		return err
	}
	defer closeUnlessRoot(e.root, destParent)
	if err := removeNonDirectoryIn(destParent, destBase, target); err != nil {
		return err
	}
	if err := e.root.Link(source.relPath, relPath); err == nil {
		return nil
	}
	return copyHardLinkTargetIn(sourceParent, sourceBase, destParent, destBase, source.target, target, os.FileMode(header.Mode))
}

func (e *windowsArchiveExtractor) ExtractSymlink(header *tar.Header, relPath string, target string) error {
	root, err := filepath.EvalSymlinks(e.rootPath)
	if err != nil {
		return fmt.Errorf("resolve destination %s: %w", e.rootPath, err)
	}
	if filepath.IsAbs(header.Linkname) || hasParentPathComponent(header.Linkname) {
		return fmt.Errorf("tar symlink %s -> %s: absolute or parent-relative link targets are refused", header.Name, header.Linkname)
	}
	parent, base, err := e.openParent(relPath, true)
	if err != nil {
		return err
	}
	defer closeUnlessRoot(e.root, parent)
	linkDir, err := filepath.EvalSymlinks(filepath.Dir(target))
	if err != nil {
		return fmt.Errorf("resolve parent of %s: %w", target, err)
	}
	if !within(root, linkDir) {
		return fmt.Errorf("tar symlink %s: parent escapes destination", header.Name)
	}
	dest := filepath.Clean(filepath.Join(linkDir, header.Linkname))
	resolvedDest, err := evalSymlinksLenient(dest)
	if err != nil {
		return fmt.Errorf("tar symlink %s -> %s: %w", header.Name, header.Linkname, err)
	}
	if !within(root, resolvedDest) {
		return fmt.Errorf("tar symlink %s -> %s escapes destination", header.Name, header.Linkname)
	}
	safeLink, err := filepath.Rel(linkDir, resolvedDest)
	if err != nil {
		return fmt.Errorf("tar symlink %s: %w", header.Name, err)
	}
	if err := removeNonDirectoryIn(parent, base, target); err != nil {
		return err
	}
	if err := parent.Symlink(safeLink, base); err != nil {
		return fmt.Errorf("create symlink %s: %w", target, err)
	}
	return nil
}

// openParent walks relPath's directories from the pinned root, one handle per
// component, refusing symlinks and junctions. The caller closes the returned
// Root with closeUnlessRoot.
func (e *windowsArchiveExtractor) openParent(relPath string, create bool) (*os.Root, string, error) {
	parts := splitArchiveRel(relPath)
	if len(parts) == 0 {
		return e.root, ".", nil
	}
	current := e.root
	currentPath := e.rootPath
	for _, part := range parts[:len(parts)-1] {
		currentPath = filepath.Join(currentPath, part)
		next, err := openDirectoryIn(current, part, currentPath, create)
		closeUnlessRoot(e.root, current)
		if err != nil {
			return nil, "", err
		}
		current = next
	}
	return current, parts[len(parts)-1], nil
}

func closeUnlessRoot(root *os.Root, r *os.Root) {
	if r != nil && r != root {
		_ = r.Close()
	}
}
