// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build windows

package toolbox

import (
	"fmt"
	"os"
	"syscall"
)

// Windows only protects the final path component here (CreateFile on a path).
// A parent-directory swap race remains until this path uses relative handle
// opens (NtCreateFile or equivalent); Unix extraction is fd-relative.
func openRegularNoFollow(target string, mode os.FileMode) (*os.File, error) {
	path, err := syscall.UTF16PtrFromString(target)
	if err != nil {
		return nil, err
	}

	handle, err := syscall.CreateFile(
		path,
		syscall.GENERIC_WRITE,
		syscall.FILE_SHARE_READ|syscall.FILE_SHARE_WRITE,
		nil,
		syscall.OPEN_ALWAYS,
		syscall.FILE_ATTRIBUTE_NORMAL|syscall.FILE_FLAG_OPEN_REPARSE_POINT,
		0,
	)
	if err != nil {
		return nil, err
	}

	closeHandle := true
	defer func() {
		if closeHandle {
			_ = syscall.CloseHandle(handle)
		}
	}()

	var info syscall.ByHandleFileInformation
	if err := syscall.GetFileInformationByHandle(handle, &info); err != nil {
		return nil, err
	}
	if info.FileAttributes&syscall.FILE_ATTRIBUTE_REPARSE_POINT != 0 {
		return nil, fmt.Errorf("refusing to write through symlink %s", target)
	}
	if info.FileAttributes&syscall.FILE_ATTRIBUTE_DIRECTORY != 0 {
		return nil, fmt.Errorf("%s exists and is not a regular file", target)
	}

	file := os.NewFile(uintptr(handle), target)
	if file == nil {
		return nil, fmt.Errorf("wrap %s handle: %w", target, syscall.EINVAL)
	}
	if err := file.Truncate(0); err != nil {
		_ = file.Close()
		return nil, err
	}
	if _, err := file.Seek(0, 0); err != nil {
		_ = file.Close()
		return nil, err
	}

	closeHandle = false
	return file, nil
}
