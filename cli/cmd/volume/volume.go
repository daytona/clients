// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package volume

import (
	"github.com/spf13/cobra"
	"go.daytona.io/cli/internal"
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
