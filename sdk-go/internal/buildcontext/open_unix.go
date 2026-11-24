// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build unix

package buildcontext

import (
	"os"
	"syscall"
)

func openRegular(root *os.Root, filename string) (*os.File, error) {
	// Refuse a replaced symlink; a replaced FIFO must never block open.
	return root.OpenFile(filename, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
}

func openFollowed(root *os.Root, filename string) (*os.File, error) {
	return root.OpenFile(filename, os.O_RDONLY|syscall.O_NONBLOCK, 0)
}
