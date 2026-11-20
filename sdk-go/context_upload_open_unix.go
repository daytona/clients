// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build unix

package daytona

import (
	"os"
	"syscall"
)

func openContextArchive(path string) (*os.File, error) {
	// A replaced FIFO must not block open, and a replaced symlink is refused.
	// openPart checks the descriptor's type and identity before reading anything.
	return os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
}
