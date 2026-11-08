// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"net/http"
	"net/url"
	"os"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

func init() {
	Register(newVolumeCommand)
}

func newVolumeCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use: "volume", Short: "Manage volumes",
		Long:    "Manage volumes.\nExamples:\n  daytona volume create data --size-gib 200\n  daytona volume list",
		GroupID: GroupWorkload,
	}
	cmd.AddCommand(newVolumeCreateCommand(), newVolumeListCommand(), newVolumeGetCommand(), newVolumeDeleteCommand())
	return cmd
}

func newVolumeCreateCommand() *cobra.Command {
	sizeGiB := int32(100)
	typeName := "disk"
	bucketName := ""
	endpoint := ""
	bucketRegion := ""
	prefix := ""
	accessKeySecret := ""
	secretKeySecret := ""
	pathStyle := false
	cmd := &cobra.Command{
		Use: "create <name>", Short: "Create a volume",
		Long: "Create a volume.\nExamples:\n  daytona volume create data --size-gib 200\n  daytona volume create data --type bucket\n  daytona volume create data --type bucket --bucket corp-data --bucket-region us-east-1 --access-key-secret AWS_KEY --secret-key-secret AWS_SECRET",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			body, err := input.Load(a.Globals.File, os.Stdin, false)
			if err != nil {
				return err
			}
			body.Set("name", args[0])
			workload.SetIfChanged(body, cmd.Flags(), "size-gib", "size_gib", sizeGiB)
			workload.SetIfChanged(body, cmd.Flags(), "type", "type", typeName)
			if cmd.Flags().Changed("bucket") {
				bucket := workload.NestedBody(body, "bucket")
				bucket.Set("name", bucketName)
				bucket.Set("origin", "external")
			}
			workload.SetNestedIfChanged(body, cmd.Flags(), "endpoint", "bucket", "endpoint", endpoint)
			workload.SetNestedIfChanged(body, cmd.Flags(), "bucket-region", "bucket", "region", bucketRegion)
			workload.SetNestedIfChanged(body, cmd.Flags(), "prefix", "bucket", "prefix", prefix)
			workload.SetNestedIfChanged(body, cmd.Flags(), "path-style", "bucket", "path_style", pathStyle)
			if cmd.Flags().Changed("access-key-secret") {
				workload.NestedBody(workload.NestedBody(body, "bucket"), "credentials").Set("access_key_secret", accessKeySecret)
			}
			if cmd.Flags().Changed("secret-key-secret") {
				workload.NestedBody(workload.NestedBody(body, "bucket"), "credentials").Set("secret_key_secret", secretKeySecret)
			}
			req, err := workload.DecodeBody[gen.CreateVolumeRequest](body)
			if err != nil {
				return err
			}
			if err := validateVolumeCreateRequest(req); err != nil {
				return err
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			var resp api.Raw[gen.Volume]
			if err := client.Do(cmd.Context(), http.MethodPost, "/volumes", nil, req, &resp); err != nil {
				return err
			}
			return a.Printer().Object(resp, volumeKV(resp.V))
		},
	}
	cmd.Flags().Int32Var(&sizeGiB, "size-gib", sizeGiB, "volume size in GiB (disk volumes only)")
	cmd.Flags().StringVar(&typeName, "type", typeName, "volume type: disk|bucket")
	cmd.Flags().StringVar(&bucketName, "bucket", bucketName, "customer bucket name; on --type bucket, omitting this creates a platform-owned bucket volume")
	cmd.Flags().StringVar(&endpoint, "endpoint", endpoint, "S3 endpoint URL for a customer bucket")
	cmd.Flags().StringVar(&bucketRegion, "bucket-region", bucketRegion, "region of a customer bucket (the S3 region, not a Daytona region)")
	cmd.Flags().StringVar(&prefix, "prefix", prefix, "bucket prefix")
	cmd.Flags().BoolVar(&pathStyle, "path-style", pathStyle, "use path-style S3 addressing for a customer bucket")
	cmd.Flags().StringVar(&accessKeySecret, "access-key-secret", accessKeySecret, "project secret name holding the bucket access key")
	cmd.Flags().StringVar(&secretKeySecret, "secret-key-secret", secretKeySecret, "project secret name holding the bucket secret key")
	addFileFlag(cmd, "")
	return cmd
}

func newVolumeListCommand() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{
		Use: "list", Short: "List volumes", Long: "List volumes.\nExample:\n  daytona volume list --limit 50",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			fetch := func(ctx context.Context, cursor string) (output.List[api.Raw[gen.Volume]], error) {
				return getRawList[gen.Volume](ctx, client, "/volumes", listQuery(flags, cursor), "volumes")
			}
			var list output.List[api.Raw[gen.Volume]]
			if flags.all {
				list, err = collectPaged(cmd.Context(), flags.cursor, fetch)
			} else {
				list, err = fetch(cmd.Context(), flags.cursor)
			}
			if err != nil {
				return err
			}
			return listOutRaw(a.Printer(), list, func(items []gen.Volume) output.Table {
				rows := make([][]string, 0, len(items))
				for _, item := range items {
					bucket := volumeBucketOptions(item)
					rows = append(rows, []string{
						item.Id, item.Name, string(item.Type), string(item.Status), formatSizeGib(item.SizeGib),
						volumeBucketName(bucket), volumeBucketOrigin(bucket), formatPtr(volumeBucketEndpoint(bucket)),
						formatPtr(volumeBucketRegion(bucket)), formatPtr(volumeBucketPrefix(bucket)),
						volumeBucketAccessKeySecret(bucket), volumeBucketSecretKeySecret(bucket), item.UpdatedAt.Format(time.RFC3339),
					})
				}
				return output.Table{Headers: []string{"ID", "NAME", "TYPE", "STATUS", "SIZE_GIB", "BUCKET", "ORIGIN", "ENDPOINT", "REGION", "PREFIX", "ACCESS_KEY_SECRET", "SECRET_KEY_SECRET", "UPDATED"}, Rows: rows}
			})
		},
	}
	addPageFlags(cmd, &flags)
	return cmd
}

func newVolumeGetCommand() *cobra.Command {
	return &cobra.Command{
		Use: "get <id>", Short: "Get one volume", Long: "Get one volume.\nExample:\n  daytona volume get <id>", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			var volume api.Raw[gen.Volume]
			if err := client.Do(cmd.Context(), http.MethodGet, "/volumes/"+url.PathEscape(args[0]), nil, nil, &volume); err != nil {
				return err
			}
			return a.Printer().Object(volume, volumeKV(volume.V))
		},
	}
}

func newVolumeDeleteCommand() *cobra.Command {
	return &cobra.Command{
		Use: "delete <id-or-name>", Short: "Delete a volume", Long: "Delete a volume by id or name. It prints the accepted deletion, or an acknowledgement naming the volume's id when the volume was already deleted.\nExample:\n  daytona volume delete <id-or-name> --yes", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			if err := a.Confirm("delete", args[0]); err != nil {
				return err
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			id, err := resolveResourceID(cmd.Context(), client, "/volumes/", args[0], func(v gen.Volume) string { return v.Id })
			if err != nil {
				return err
			}
			var resp api.Raw[gen.AsyncOperation]
			if err := client.Do(cmd.Context(), http.MethodDelete, "/volumes/"+url.PathEscape(id), idOnly, nil, &resp); err != nil {
				return err
			}
			if resp.V.Id == "" {
				return printAck(a, "id", id, "deleted", "")
			}
			return a.Printer().Object(resp, [][2]string{{"id", resp.V.Id}, {"status", string(resp.V.Status)}})
		},
	}
}
