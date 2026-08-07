// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package snapshot

import (
	"context"
	"fmt"
	"net/http"

	apiclient_cli "github.com/daytona/clients/cli/apiclient"
	view_common "github.com/daytona/clients/cli/views/common"
	"github.com/spf13/cobra"
)

var ActivateCmd = &cobra.Command{
	Use:   "activate [SNAPSHOT_ID | SNAPSHOT_NAME]",
	Short: "Activate a snapshot",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := context.Background()

		apiClient, err := apiclient_cli.GetApiClient(nil, nil)
		if err != nil {
			return err
		}

		snapshotIdOrName := args[0]

		// Optimistic path: a UUID-shaped argument may be a real snapshot ID, so try
		// the ID-only activate endpoint directly and save a round trip. On 404 the
		// argument may instead be a UUID-formatted NAME (snapshot names may be
		// UUID-shaped), so we fall through to the name-resolving GET; the server
		// accepts ID-or-name there.
		if isSnapshotId(snapshotIdOrName) {
			_, res, err := apiClient.SnapshotsAPI.ActivateSnapshot(ctx, snapshotIdOrName).Execute()
			if err == nil {
				view_common.RenderInfoMessageBold(fmt.Sprintf("Snapshot %s activated", snapshotIdOrName))
				return nil
			}
			if res == nil || res.StatusCode != http.StatusNotFound {
				return apiclient_cli.HandleErrorResponse(res, err)
			}
		}

		snapshot, res, err := apiClient.SnapshotsAPI.GetSnapshot(ctx, snapshotIdOrName).Execute()
		if err != nil {
			return apiclient_cli.HandleErrorResponse(res, err)
		}

		_, res, err = apiClient.SnapshotsAPI.ActivateSnapshot(ctx, snapshot.Id).Execute()
		if err != nil {
			return apiclient_cli.HandleErrorResponse(res, err)
		}

		view_common.RenderInfoMessageBold(fmt.Sprintf("Snapshot %s activated", snapshotIdOrName))

		return nil
	},
}
