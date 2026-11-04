// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// daytona — the Daytona platform CLI.
package main

import (
	"os"

	"github.com/daytona/clients/cli/cmd"
)

func main() { os.Exit(cmd.Execute()) }
