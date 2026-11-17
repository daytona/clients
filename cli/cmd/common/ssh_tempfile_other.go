// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build !windows

package common

import "os"

// createPrivateTemp creates a new file in dir (os.TempDir() when empty) that
// only the current user can open: os.CreateTemp creates it with mode 0600.
func createPrivateTemp(dir, prefix, suffix string) (*os.File, error) {
	return os.CreateTemp(dir, prefix+"*"+suffix)
}
