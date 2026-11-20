// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build !unix

package daytona

import "os"

func openContextArchive(path string) (*os.File, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, badRequest("build context changed while it was being uploaded")
	}
	// openPart also verifies the opened descriptor against its initial identity.
	return os.Open(path)
}
