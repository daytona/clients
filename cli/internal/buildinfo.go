// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package internal

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
