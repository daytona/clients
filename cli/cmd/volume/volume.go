// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package volume

import (
	"github.com/daytona/clients/cli/internal"
	"github.com/spf13/cobra"
)

var VolumeCmd = &cobra.Command{
	Use:     "volume",
	Short:   "Manage Daytona volumes",
	Long:    "Commands for managing Daytona volumes",
	Aliases: []string{"volumes"},
	GroupID: internal.SANDBOX_GROUP,
}

func init() {
	VolumeCmd.AddCommand(ListCmd)
	VolumeCmd.AddCommand(CreateCmd)
	VolumeCmd.AddCommand(GetCmd)
	VolumeCmd.AddCommand(DeleteCmd)
}
