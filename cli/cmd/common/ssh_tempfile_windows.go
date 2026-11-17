// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build windows

package common

import (
	"crypto/rand"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"unsafe"

	"golang.org/x/sys/windows"
)

// createPrivateTemp creates a new file in dir (os.TempDir() when empty) that
// only the current user can open. Windows ignores the 0600 mode os.CreateTemp
// sets, and restricting the ACL after creation would leave a window in which
// another user could open a handle that stays valid afterwards, for example
// when TEMP points at a shared directory. So the file is created with a
// protected DACL that grants the current user full access and nobody else
// anything, and without sharing while the CLI writes it.
func createPrivateTemp(dir, prefix, suffix string) (*os.File, error) {
	if dir == "" {
		dir = os.TempDir()
	}

	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		return nil, err
	}
	sd, err := windows.SecurityDescriptorFromString("D:P(A;;FA;;;" + user.User.Sid.String() + ")")
	if err != nil {
		return nil, err
	}
	sa := &windows.SecurityAttributes{
		Length:             uint32(unsafe.Sizeof(windows.SecurityAttributes{})),
		SecurityDescriptor: sd,
	}

	for range 100 {
		path := filepath.Join(dir, prefix+rand.Text()+suffix)
		name, err := windows.UTF16PtrFromString(path)
		if err != nil {
			return nil, err
		}
		handle, err := windows.CreateFile(name, windows.GENERIC_READ|windows.GENERIC_WRITE, 0, sa,
			windows.CREATE_NEW, windows.FILE_ATTRIBUTE_NORMAL, 0)
		if errors.Is(err, windows.ERROR_FILE_EXISTS) {
			continue
		}
		if err != nil {
			return nil, &os.PathError{Op: "create", Path: path, Err: err}
		}
		return os.NewFile(uintptr(handle), path), nil
	}

	return nil, fmt.Errorf("could not create a unique file in %s", dir)
}
