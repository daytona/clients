// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build !unix

package buildcontext

import "testing"

func fixtureFIFO(t *testing.T, _ string) {
	t.Helper()
	t.Skip("FIFO fixture requires a Unix filesystem")
}
