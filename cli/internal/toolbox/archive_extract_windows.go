// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build windows

package toolbox

import (
	"archive/tar"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

type windowsArchiveExtractor struct {
	rootPath string
}

func newArchiveExtractor(dst string) (archiveExtractor, error) {
	rootPath, err := filepath.Abs(dst)
	if err != nil {
		return nil, fmt.Errorf("resolve destination %s: %w", dst, err)
	}
	return &windowsArchiveExtractor{rootPath: rootPath}, nil
}

func (e *windowsArchiveExtractor) Close() error {
	return nil
}

func (e *windowsArchiveExtractor) ExtractDirectory(_ string, target string, mode os.FileMode) error {
	if err := mkdirAllContained(e.rootPath, target); err != nil {
		return err
	}
	if err := os.Chmod(target, mode); err != nil {
		return fmt.Errorf("chmod %s: %w", target, err)
	}
	return nil
}

func (e *windowsArchiveExtractor) OpenRegular(_ string, target string, mode os.FileMode) (*os.File, error) {
	if err := mkdirAllContained(e.rootPath, filepath.Dir(target)); err != nil {
		return nil, err
	}
	return openRegularNoFollow(target, mode)
}

func (e *windowsArchiveExtractor) ExtractHardLink(header *tar.Header, _ string, target string) error {
	if filepath.IsAbs(header.Linkname) || hasParentPathComponent(header.Linkname) {
		return fmt.Errorf("tar hard link %s -> %s: absolute or parent-relative link targets are refused", header.Name, header.Linkname)
	}
	linkTarget, err := safeTarget(e.rootPath, header.Linkname)
	if err != nil {
		return fmt.Errorf("tar hard link %s -> %s: %w", header.Name, header.Linkname, err)
	}
	info, err := os.Lstat(linkTarget)
	if err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("tar hard link %s -> %s: target has not been extracted yet", header.Name, header.Linkname)
		}
		return fmt.Errorf("tar hard link %s -> %s: %w", header.Name, header.Linkname, err)
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("tar hard link %s -> %s: target must be a regular file", header.Name, header.Linkname)
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return fmt.Errorf("create parent for %s: %w", target, err)
	}
	_ = os.Remove(target)
	if err := os.Link(linkTarget, target); err == nil {
		return nil
	}
	return copyHardLinkTarget(linkTarget, target, os.FileMode(header.Mode))
}

func (e *windowsArchiveExtractor) ExtractSymlink(header *tar.Header, _ string, target string) error {
	root, err := filepath.EvalSymlinks(e.rootPath)
	if err != nil {
		return fmt.Errorf("resolve destination %s: %w", e.rootPath, err)
	}
	if filepath.IsAbs(header.Linkname) || hasParentPathComponent(header.Linkname) {
		return fmt.Errorf("tar symlink %s -> %s: absolute or parent-relative link targets are refused", header.Name, header.Linkname)
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return fmt.Errorf("create parent for %s: %w", target, err)
	}
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
	_ = os.Remove(target)
	if err := os.Symlink(safeLink, filepath.Join(linkDir, filepath.Base(target))); err != nil {
		return fmt.Errorf("create symlink %s: %w", target, err)
	}
	return nil
}

func copyHardLinkTarget(source string, target string, mode os.FileMode) error {
	input, err := os.Open(source)
	if err != nil {
		return fmt.Errorf("open %s: %w", source, err)
	}
	defer input.Close()
	output, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, mode)
	if err != nil {
		return fmt.Errorf("create %s: %w", target, err)
	}
	if _, err := io.Copy(output, input); err != nil {
		_ = output.Close()
		return fmt.Errorf("write %s: %w", target, err)
	}
	if err := output.Close(); err != nil {
		return fmt.Errorf("close %s: %w", target, err)
	}
	return nil
}

func mkdirAllContained(dst string, dir string) error {
	rel, err := filepath.Rel(filepath.Clean(dst), filepath.Clean(dir))
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return fmt.Errorf("directory %s escapes destination", dir)
	}
	cur := filepath.Clean(dst)
	if rel == "." {
		return nil
	}
	for _, part := range strings.Split(rel, string(filepath.Separator)) {
		cur = filepath.Join(cur, part)
		info, statErr := os.Lstat(cur)
		switch {
		case statErr == nil && info.Mode()&os.ModeSymlink != 0:
			return fmt.Errorf("refusing to extract through symlink %s", cur)
		case statErr == nil && !info.IsDir():
			return fmt.Errorf("%s exists and is not a directory", cur)
		case statErr == nil:
		case os.IsNotExist(statErr):
			if err := os.Mkdir(cur, 0o755); err != nil && !os.IsExist(err) {
				return fmt.Errorf("create %s: %w", cur, err)
			}
		default:
			return fmt.Errorf("stat %s: %w", cur, statErr)
		}
	}
	return nil
}
