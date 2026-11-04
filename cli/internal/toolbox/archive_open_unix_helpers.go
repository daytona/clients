// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build !windows

package toolbox

import (
	"errors"
	"fmt"
	"io"
	"os"

	"golang.org/x/sys/unix"
)

func openRegularAt(parent *os.File, base string, target string, mode os.FileMode) (*os.File, error) {
	err := requireRegularTarget(parent, base, target)
	switch {
	case err == nil:
		if err := unix.Unlinkat(int(parent.Fd()), base, 0); err != nil {
			return nil, err
		}
	case errors.Is(err, os.ErrNotExist):
	default:
		return nil, err
	}
	fd, err := unix.Openat(int(parent.Fd()), base, unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW|unix.O_CLOEXEC, uint32(mode.Perm()))
	if err != nil {
		if errors.Is(err, unix.ELOOP) {
			return nil, fmt.Errorf("refusing to write through symlink %s", target)
		}
		return nil, err
	}
	file := os.NewFile(uintptr(fd), target)
	if file == nil {
		_ = unix.Close(fd)
		return nil, fmt.Errorf("wrap %s handle: %w", target, unix.EINVAL)
	}
	return file, nil
}

func requireRegularAt(parent *os.File, base string, target string) error {
	return requireRegularTarget(parent, base, target)
}

func requireRegularTarget(parent *os.File, base string, target string) error {
	var stat unix.Stat_t
	err := unix.Fstatat(int(parent.Fd()), base, &stat, unix.AT_SYMLINK_NOFOLLOW)
	if err != nil {
		if errors.Is(err, unix.ENOENT) {
			return os.ErrNotExist
		}
		return err
	}
	if stat.Mode&unix.S_IFMT == unix.S_IFLNK {
		return fmt.Errorf("refusing to write through symlink %s", target)
	}
	if stat.Mode&unix.S_IFMT != unix.S_IFREG {
		return fmt.Errorf("%s exists and is not a regular file", target)
	}
	return nil
}

func duplicateDir(dir *os.File) (*os.File, error) {
	fd, err := unix.Openat(int(dir.Fd()), ".", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	file := os.NewFile(uintptr(fd), dir.Name())
	if file == nil {
		_ = unix.Close(fd)
		return nil, fmt.Errorf("wrap %s handle: %w", dir.Name(), unix.EINVAL)
	}
	return file, nil
}

func openDirectoryAt(parent *os.File, name string, target string, create bool) (*os.File, error) {
	fd, err := unix.Openat(int(parent.Fd()), name, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err == nil {
		file := os.NewFile(uintptr(fd), target)
		if file == nil {
			_ = unix.Close(fd)
			return nil, fmt.Errorf("wrap %s handle: %w", target, unix.EINVAL)
		}
		return file, nil
	}
	var stat unix.Stat_t
	statErr := unix.Fstatat(int(parent.Fd()), name, &stat, unix.AT_SYMLINK_NOFOLLOW)
	if statErr == nil {
		if stat.Mode&unix.S_IFMT == unix.S_IFLNK {
			return nil, fmt.Errorf("refusing to extract through symlink %s", target)
		}
		if stat.Mode&unix.S_IFMT != unix.S_IFDIR {
			return nil, fmt.Errorf("%s exists and is not a directory", target)
		}
		return nil, err
	}
	if !errors.Is(statErr, unix.ENOENT) {
		return nil, statErr
	}
	if !create {
		return nil, os.ErrNotExist
	}
	if mkErr := unix.Mkdirat(int(parent.Fd()), name, 0o755); mkErr != nil && !errors.Is(mkErr, unix.EEXIST) {
		return nil, fmt.Errorf("create %s: %w", target, mkErr)
	}
	fd, err = unix.Openat(int(parent.Fd()), name, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		if errors.Is(err, unix.ELOOP) {
			return nil, fmt.Errorf("refusing to extract through symlink %s", target)
		}
		return nil, fmt.Errorf("open %s: %w", target, err)
	}
	file := os.NewFile(uintptr(fd), target)
	if file == nil {
		_ = unix.Close(fd)
		return nil, fmt.Errorf("wrap %s handle: %w", target, unix.EINVAL)
	}
	return file, nil
}

func unlinkIfExists(parent *os.File, base string) error {
	err := unix.Unlinkat(int(parent.Fd()), base, 0)
	if err == nil || errors.Is(err, unix.ENOENT) {
		return nil
	}
	return err
}

func copyHardLinkTargetAt(sourceParent *os.File, sourceBase string, destParent *os.File, destBase string, source string, target string, mode os.FileMode) error {
	sourceFD, err := unix.Openat(int(sourceParent.Fd()), sourceBase, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return fmt.Errorf("open %s: %w", source, err)
	}
	input := os.NewFile(uintptr(sourceFD), source)
	if input == nil {
		_ = unix.Close(sourceFD)
		return fmt.Errorf("wrap %s handle: %w", source, unix.EINVAL)
	}
	defer input.Close()
	// Re-check on the OPEN descriptor: the earlier Lstat can be raced by a
	// swap to a FIFO or device, which would block or misbehave under copy.
	info, err := input.Stat()
	if err != nil {
		return fmt.Errorf("stat %s: %w", source, err)
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("hard link source %s is not a regular file", source)
	}
	output, err := openRegularAt(destParent, destBase, target, mode)
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
