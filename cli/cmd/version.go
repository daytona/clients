// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
	"go.daytona.io/cli/internal"
)

var VersionCmd = &cobra.Command{
	Use:   "version",
	Short: "Print the version number",
	RunE: func(cmd *cobra.Command, args []string) error {
		fmt.Println("Daytona CLI version", internal.Version)
		return nil
	},
}
