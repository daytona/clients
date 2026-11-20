// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build unix

package buildcontext

import (
	"os"
	"syscall"
)

func openRegular(filename string) (*os.File, error) {
	// Refuse a replaced symlink; a replaced FIFO must never block open.
	return os.OpenFile(filename, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
}
