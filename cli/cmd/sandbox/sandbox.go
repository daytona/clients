// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package sandbox

import (
	"github.com/spf13/cobra"
	"go.daytona.io/cli/internal"
)

var SandboxCmd = &cobra.Command{
	Use:     "sandbox",
	Short:   "Manage Daytona sandboxes",
	Long:    "Commands for managing Daytona sandboxes",
	Aliases: []string{"sandboxes"},
	GroupID: internal.SANDBOX_GROUP,
	Hidden:  true, // Deprecated: use top-level commands instead (e.g., "daytona start" instead of "daytona sandbox start")
}

func init() {
	SandboxCmd.AddCommand(ListCmd)
	SandboxCmd.AddCommand(CreateCmd)
	SandboxCmd.AddCommand(InfoCmd)
	SandboxCmd.AddCommand(DeleteCmd)
	SandboxCmd.AddCommand(StartCmd)
	SandboxCmd.AddCommand(StopCmd)
	SandboxCmd.AddCommand(PauseCmd)
	SandboxCmd.AddCommand(ArchiveCmd)
	SandboxCmd.AddCommand(SSHCmd)
	SandboxCmd.AddCommand(ExecCmd)
	SandboxCmd.AddCommand(PreviewUrlCmd)
}
