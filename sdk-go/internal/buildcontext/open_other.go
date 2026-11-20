// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build !unix

package buildcontext

import "os"

func openRegular(filename string) (*os.File, error) {
	info, err := os.Lstat(filename)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, invalid("build context changed: "+quote(filename), nil)
	}
	// The caller also compares the opened identity with its walk-time snapshot.
	return os.Open(filename)
}
