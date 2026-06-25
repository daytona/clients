// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package snapshot

import (
	"github.com/spf13/cobra"
	"go.daytona.io/cli/internal"
)

var SnapshotsCmd = &cobra.Command{
	Use:     "snapshot",
	Short:   "Manage Daytona snapshots",
	Long:    "Commands for managing Daytona snapshots",
	Aliases: []string{"snapshots"},
	GroupID: internal.SANDBOX_GROUP,
}

func init() {
	SnapshotsCmd.AddCommand(ListCmd)
	SnapshotsCmd.AddCommand(CreateCmd)
	SnapshotsCmd.AddCommand(PushCmd)
	SnapshotsCmd.AddCommand(DeleteCmd)
}
