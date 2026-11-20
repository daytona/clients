// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// GitResetMode selects how Git.Reset updates repository state.
// The zero value leaves Git's mixed-reset default; other strings are passed
// through for server validation, including future modes.
type GitResetMode string

const (
	// GitResetModeMixed resets the index but preserves the working tree.
	GitResetModeMixed GitResetMode = "mixed"
	// GitResetModeSoft preserves the index and working tree.
	GitResetModeSoft GitResetMode = "soft"
	// GitResetModeHard resets the index and working tree, discarding changes.
	GitResetModeHard GitResetMode = "hard"
	// GitResetModeMerge preserves unstaged changes where possible.
	GitResetModeMerge GitResetMode = "merge"
	// GitResetModeKeep refuses to overwrite local changes.
	GitResetModeKeep GitResetMode = "keep"
)
