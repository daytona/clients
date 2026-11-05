// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build windows

package toolbox

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
)

// openDirectoryIn opens (and with create, makes) the directory name inside
// parent. A symlink or junction at name is refused, never followed.
func openDirectoryIn(parent *os.Root, name string, target string, create bool) (*os.Root, error) {
	info, err := parent.Lstat(name)
	if errors.Is(err, fs.ErrNotExist) {
		if !create {
			return nil, fs.ErrNotExist
		}
		if err := parent.Mkdir(name, 0o755); err != nil && !errors.Is(err, fs.ErrExist) {
			return nil, fmt.Errorf("create %s: %w", target, err)
		}
		info, err = parent.Lstat(name)
	}
	if err != nil {
		return nil, fmt.Errorf("stat %s: %w", target, err)
	}
	if info.Mode()&os.ModeSymlink != 0 || info.Mode()&os.ModeIrregular != 0 {
		return nil, fmt.Errorf("refusing to extract through symlink %s", target)
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("%s exists and is not a directory", target)
	}
	dir, err := parent.OpenRoot(name)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", target, err)
	}
	// OpenRoot follows a link swapped in after the Lstat above; accept the
	// handle only if it is the directory that was checked.
	opened, err := dir.Stat(".")
	if err != nil {
		_ = dir.Close()
		return nil, fmt.Errorf("stat %s: %w", target, err)
	}
	if !os.SameFile(info, opened) {
		_ = dir.Close()
		return nil, fmt.Errorf("refusing to extract through symlink %s (replaced while opening)", target)
	}
	return dir, nil
}

// openRegularIn creates a fresh file at name inside parent. An existing
// regular file is removed first and the new one is created with FILE_CREATE
// (O_EXCL), so a hard-linked sibling keeps its content and a link planted in
// between fails the create instead of being written through.
func openRegularIn(parent *os.Root, name string, target string, mode os.FileMode) (*os.File, error) {
	switch err := requireRegularIn(parent, name, target); {
	case err == nil:
		if err := parent.Remove(name); err != nil && !errors.Is(err, fs.ErrNotExist) {
			return nil, err
		}
	case errors.Is(err, fs.ErrNotExist):
	default:
		return nil, err
	}
	return parent.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode.Perm())
}

func requireRegularIn(parent *os.Root, name string, target string) error {
	info, err := parent.Lstat(name)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return fs.ErrNotExist
		}
		return err
	}
	if info.Mode()&os.ModeSymlink != 0 || info.Mode()&os.ModeIrregular != 0 {
		return fmt.Errorf("refusing to write through symlink %s", target)
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("%s exists and is not a regular file", target)
	}
	return nil
}

// removeNonDirectoryIn clears name for a new link; a directory there is an
// error, as unlinkat(2) makes it on Unix.
func removeNonDirectoryIn(parent *os.Root, name string, target string) error {
	info, err := parent.Lstat(name)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if info.IsDir() {
		return fmt.Errorf("%s exists and is a directory", target)
	}
	if err := parent.Remove(name); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return nil
}

func copyHardLinkTargetIn(sourceParent *os.Root, sourceBase string, destParent *os.Root, destBase string, source string, target string, mode os.FileMode) error {
	input, err := sourceParent.Open(sourceBase)
	if err != nil {
		return fmt.Errorf("open %s: %w", source, err)
	}
	defer input.Close()
	info, err := input.Stat()
	if err != nil {
		return fmt.Errorf("stat %s: %w", source, err)
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("hard link source %s is not a regular file", source)
	}
	output, err := openRegularIn(destParent, destBase, target, mode)
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
