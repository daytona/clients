// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package config

import (
	"regexp"

	"github.com/daytona/clients/cli/internal/cerr"
)

var profileName = regexp.MustCompile(`^[A-Za-z0-9._-]{1,64}$`)

// ValidateProfileName checks names supplied for creating or selecting a profile.
// Loading a saved configuration deliberately does not apply this restriction.
func ValidateProfileName(name string) error {
	if name == "." || name == ".." || !profileName.MatchString(name) {
		return cerr.Usage("profile name must be 1–64 ASCII letters, digits, '.', '_' or '-', and cannot be '.' or '..'")
	}
	return nil
}
