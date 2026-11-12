// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

func init() {
	Register(newSnapshotCommand)
}

func newSnapshotCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:     "snapshot",
		Short:   "Manage snapshots",
		Long:    "Manage snapshots.\nExamples:\n  daytona snapshot create <sandbox> --name before-upgrade\n  daytona snapshot list --all",
		GroupID: GroupWorkload,
	}
	cmd.AddCommand(newSnapshotCreateCommand(), newSnapshotListCommand(), newSnapshotGetCommand(), newSnapshotDeleteCommand())
	return cmd
}

func newSnapshotCreateCommand() *cobra.Command {
	name := ""
	cmd := &cobra.Command{
		Use:   "create <sandbox>",
		Short: "Create a snapshot",
		Long:  "Create a Linux memory snapshot with memory and disk. For disk-only capture, use `daytona image capture`.\nRequires write:snapshots and write:sandboxes.\nExamples:\n  daytona snapshot create <sandbox> --name before-upgrade",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			resolved, err := workload.ResolveSandbox(cmd.Context(), home, workload.NewCatalogCache(), args[0])
			if err != nil {
				return err
			}
			body, err := input.Load(a.Globals.File, os.Stdin, false)
			if err != nil {
				return err
			}
			workload.SetIfChanged(body, cmd.Flags(), "name", "name", name)
			req, err := workload.DecodeBody[gen.CreateSandboxSnapshotRequest](body)
			if err != nil {
				return err
			}
			if body["name"] == nil {
				return cerr.Usage("--name is required")
			}
			var created api.Raw[gen.AsyncOperation]
			if err := resolved.Client.Do(cmd.Context(), http.MethodPost, fmt.Sprintf("/sandboxes/%s/snapshots", url.PathEscape(resolved.Detail.Id)), nil, req, &created); err != nil {
				return err
			}
			return printCreatedSnapshot(cmd.Context(), a, resolved.Client, created)
		},
	}
	cmd.Flags().StringVar(&name, "name", "", "snapshot name")
	// Not MarkFlagRequired: `name` may come from --file (the RunE checks the
	// merged body), and cobra would reject the command before RunE runs.
	addFileFlag(cmd, "")
	addWaitFlags(cmd)
	return cmd
}

func newSnapshotListCommand() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List snapshots",
		Long:  "List snapshots.\nExample:\n  daytona snapshot list --limit 20",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			probe := &searchProbe{}
			fetch := func(ctx context.Context, cursor string) (output.List[api.Raw[gen.SnapshotListItem]], error) {
				// A deployment with search serves the list from it.
				if list, served, err := getRawSearchPage[gen.SnapshotListItem](ctx, probe, client, "/snapshots/search", listQuery(flags, cursor), "snapshots"); served {
					return list, err
				}
				return getRawList[gen.SnapshotListItem](ctx, client, "/snapshots", listQuery(flags, cursor), "snapshots")
			}
			var list output.List[api.Raw[gen.SnapshotListItem]]
			if flags.all {
				list, err = collectPaged(cmd.Context(), flags.cursor, fetch)
			} else {
				list, err = fetch(cmd.Context(), flags.cursor)
			}
			if err != nil {
				return err
			}
			return listOutRaw(a.Printer(), list, func(items []gen.SnapshotListItem) output.Table {
				rows := make([][]string, 0, len(items))
				for _, item := range items {
					rows = append(rows, []string{item.Id, item.Name, string(item.Kind), item.Status, item.RegionId, item.CreatedAt.Format(time.RFC3339)})
				}
				return output.Table{Headers: []string{"ID", "NAME", "KIND", "STATUS", "REGION", "CREATED"}, Rows: rows}
			})
		},
	}
	addPageFlags(cmd, &flags)
	return cmd
}

func newSnapshotGetCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "get <id>",
		Short: "Get one snapshot",
		Long:  "Get one snapshot.\nExample:\n  daytona snapshot get <id>",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			if err := refuseSearchName("snapshot", args[0]); err != nil {
				return err
			}
			var snapshot api.Raw[gen.Snapshot]
			if err := client.Do(cmd.Context(), http.MethodGet, "/snapshots/"+url.PathEscape(args[0]), nil, nil, &snapshot); err != nil {
				return err
			}
			return a.Printer().Object(snapshot, snapshotKV(snapshot.V))
		},
	}
}

func newSnapshotDeleteCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "delete <id-or-name>",
		Short: "Delete a snapshot",
		Long:  "Delete a snapshot by id or name; the acknowledgement repeats the reference as given.\nExample:\n  daytona snapshot delete <id-or-name> --yes",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			if err := a.Confirm("delete", args[0]); err != nil {
				return err
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			if err := client.Do(cmd.Context(), http.MethodDelete, "/snapshots/"+url.PathEscape(args[0]), nil, nil, nil); err != nil {
				return err
			}
			return printAck(a, "ref", args[0], "deleted", "")
		},
	}
}

func printCreatedSnapshot(ctx context.Context, a *app.App, client *api.Client, raw api.Raw[gen.AsyncOperation]) error {
	created := raw.V
	if a.Globals.NoWait {
		return a.Printer().Object(raw, [][2]string{{"id", created.Id}, {"status", created.Status}})
	}
	timeout, err := workload.ParseTimeout(a.Globals.Timeout)
	if err != nil {
		return err
	}
	snapshot, err := workload.WaitForSnapshot(ctx, client, created.Id, timeout)
	if err != nil {
		return err
	}
	return a.Printer().Object(snapshot, snapshotKV(snapshot.V))
}

func snapshotKV(snapshot gen.Snapshot) [][2]string {
	return [][2]string{{"id", snapshot.Id}, {"name", snapshot.Name}, {"kind", string(snapshot.Kind)}, {"status", snapshot.Status}, {"region", snapshot.RegionId}, {"available_in", strings.Join(snapshot.RegionIds, ",")}}
}
