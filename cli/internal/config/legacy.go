// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package config

import (
	"os"
	"path/filepath"
)

// userConfigDir is the operating system's user configuration directory, the
// base of the 0.x CLI's default config directory (a seam for tests).
var userConfigDir = os.UserConfigDir

// LegacyConfigFile returns the 0.x CLI's config.json when it exists as a
// regular file. The 0.x CLI reads $DAYTONA_CONFIG_DIR/config.json, else
// config.json in the daytona directory under the operating system's user
// configuration directory. This CLI never reads or changes that file; the
// path only lets it point a user at the 0.x CLI.
func LegacyConfigFile(getenv func(string) string) (string, bool) {
	dir := getenv("DAYTONA_CONFIG_DIR")
	if dir == "" {
		base, err := userConfigDir()
		if err != nil || base == "" {
			return "", false
		}
		dir = filepath.Join(base, "daytona")
	}
	path := filepath.Join(dir, "config.json")
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return "", false
	}
	return path, true
}
