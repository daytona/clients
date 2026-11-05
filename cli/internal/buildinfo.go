// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package internal

import (
	"os"
	"path/filepath"
	"strings"
)

var (
	Version           = "v0.0.0-dev"
	DaytonaApiUrl     = ""
	Auth0Domain       = ""
	Auth0ClientId     = ""
	Auth0CallbackPort = ""
	Auth0Audience     = ""

	// BinaryName is the command name users run. Release builds of the 0.x line
	// set it to "daytona-v0" once the 2.x CLI takes over the "daytona" name.
	BinaryName = "daytona"
	// HomebrewFormula is the formula that installs this build.
	HomebrewFormula = "daytonaio/cli/daytona"
)

func init() {
	adoptInvokedName(os.Args[0])
}

// adoptInvokedName names a daytona-v0 build "daytona" when it runs as daytona:
// until the first stable 2.x release, the same 0.x binary also ships as the
// daytona-<os>-<arch> assets that the daytona Homebrew formula installs, and
// its hints and MCP config must name a command that is on PATH.
func adoptInvokedName(arg0 string) {
	if BinaryName != "daytona-v0" {
		return
	}
	name := filepath.Base(arg0)
	if strings.EqualFold(filepath.Ext(name), ".exe") {
		name = strings.TrimSuffix(name, filepath.Ext(name))
	}
	if strings.EqualFold(name, "daytona") {
		BinaryName = "daytona"
		HomebrewFormula = "daytonaio/cli/daytona"
	}
}
