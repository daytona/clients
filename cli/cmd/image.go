// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/url"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

func init() {
	Register(newImageCommand)
}

func newImageCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:     "image",
		Short:   "Manage images",
		Long:    "Manage images.\nExamples:\n  daytona image create my-base --ref python:3.12\n  daytona image list --all",
		GroupID: GroupWorkload,
	}
	cmd.AddCommand(
		newImageImportCommand(),
		newImageBuildCommand(),
		newImageCaptureCommand(),
		newImagePromoteCommand(),
		newImageListCommand(),
		newImageGetCommand(),
		newImageDeleteCommand(),
		newImageLogsCommand(),
	)
	return cmd
}

func newImageGetCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "get <id>",
		Short: "Get one image",
		Long:  "Get one image.\nExample:\n  daytona image get <id>",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			var image api.Raw[gen.Image]
			if err := client.Do(cmd.Context(), http.MethodGet, "/images/"+url.PathEscape(args[0]), nil, nil, &image); err != nil {
				return err
			}
			return a.Printer().Object(image, imageKV(image.V))
		},
	}
}

func newImageDeleteCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "delete <id-or-name>",
		Short: "Delete an image",
		Long:  "Delete an image by id or name; the acknowledgement names its id.\nExample:\n  daytona image delete <id-or-name> --yes",
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
			id, err := resolveResourceID(cmd.Context(), client, "/images/", args[0], func(v gen.Image) string { return v.Id })
			if err != nil {
				return err
			}
			if err := client.Do(cmd.Context(), http.MethodDelete, "/images/"+url.PathEscape(id), idOnly, nil, nil); err != nil {
				return err
			}
			return printAck(a, "id", id, "deleted", "")
		},
	}
}

func newImageCaptureCommand() *cobra.Command {
	var name string
	cmd := &cobra.Command{
		Use:   "capture <sandbox>",
		Short: "Capture a sandbox filesystem as an image",
		Long:  "Capture only a sandbox disk as an image. For memory capture, use `daytona snapshot create`.\nA macOS sandbox must be persistent and stopped; it cannot start until the snapshot is `ready`.\nExample:\n  daytona image capture <sandbox> --name saved-image",
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
			body := map[string]any{}
			if cmd.Flags().Changed("name") {
				body["name"] = name
			}
			req, err := workload.DecodeBody[gen.CreateSandboxImageRequest](body)
			if err != nil {
				return err
			}
			var created api.Raw[gen.AsyncOperation]
			if err := resolved.Client.Do(cmd.Context(), http.MethodPost, "/sandboxes/"+url.PathEscape(resolved.Detail.Id)+"/images", nil, req, &created); err != nil {
				return err
			}
			if a.Globals.NoWait {
				return a.Printer().Object(created, [][2]string{{"id", created.V.Id}, {"status", created.V.Status}})
			}
			timeout, err := workload.ParseTimeout(a.Globals.Timeout)
			if err != nil {
				return err
			}
			final, err := workload.WaitForImage(cmd.Context(), resolved.Client, created.V.Id, timeout)
			if err != nil {
				return err
			}
			return a.Printer().Object(final, imageKV(final.V))
		},
	}
	cmd.Flags().StringVar(&name, "name", "", "image name")
	addWaitFlags(cmd)
	return cmd
}

func newImagePromoteCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "promote <image> <name>",
		Short: "Name an anonymous image, making it permanent",
		Long: "Name an anonymous image, making it permanent (PATCH /images/{id}).\n" +
			"Promotion only: an image that already has a name answers 409 — renaming a named image is not offered.\n" +
			"Example:\n  daytona image promote img_abc123 saved-image",
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			var image api.Raw[gen.Image]
			if err := client.Do(cmd.Context(), http.MethodPatch, "/images/"+url.PathEscape(args[0]), nil, gen.UpdateImageRequest{Name: args[1]}, &image); err != nil {
				return err
			}
			return a.Printer().Object(image, imageKV(image.V))
		},
	}
	return cmd
}

func newImageLogsCommand() *cobra.Command {
	follow := false
	cmd := &cobra.Command{
		Use:   "logs <id>",
		Short: "Read image build logs",
		Long: "Read image build logs. With --follow, table output streams the log text as it arrives;\n" +
			"-o json streams NDJSON, one {\"data\": \"...\"} object per chunk; -o yaml is not supported.\n" +
			"Example:\n  daytona image logs <id> --follow",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			if follow && a.Format == output.FormatYAML {
				return cerr.Usage("--output yaml is not supported with --follow")
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			if follow {
				// --timeout bounds the follow (the stream itself has no client
				// timeout); expiry is exit 8 like every other wait.
				timeout, err := workload.ParseTimeout(a.Globals.Timeout)
				if err != nil {
					return err
				}
				ctx, cancel := context.WithTimeout(cmd.Context(), timeout)
				defer cancel()
				err = streamDiscoveredBuildLogs(ctx, client, "/images/"+url.PathEscape(args[0])+"/build-logs-url", a.Format, cmd.OutOrStdout())
				if errors.Is(ctx.Err(), context.DeadlineExceeded) && cmd.Context().Err() == nil {
					return cerr.New(cerr.ExitTimeout, "timed out after %s following the build log", timeout).
						WithHint("the build keeps running; re-run with a longer --timeout or without --follow")
				}
				return err
			}
			text, err := readDiscoveredBuildLogs(cmd.Context(), client, "/images/"+url.PathEscape(args[0])+"/build-logs-url", false)
			if err != nil {
				return err
			}
			return a.Printer().Raw(text, map[string]any{"logs": text})
		},
	}
	cmd.Flags().BoolVar(&follow, "follow", false, "follow until the build finishes")
	addTimeoutFlag(cmd, defaultWaitTimeout, "with --follow, how long to follow the log (Go duration); the build keeps running after it")
	return cmd
}

func readDiscoveredBuildLogs(ctx context.Context, client *api.Client, discoveryPath string, follow bool) (string, error) {
	var payload gen.BuildLogsUrl
	if err := client.Do(ctx, http.MethodGet, discoveryPath, nil, nil, &payload); err != nil {
		return "", err
	}
	baseURL, err := validatedBuildLogBaseURL(discoveryPath, payload.Url)
	if err != nil {
		return "", err
	}
	query := url.Values{"format": {"text"}}
	if follow {
		query.Set("follow", "true")
	} else {
		query.Set("follow", "false")
	}
	return readText(ctx, client.WithBaseURL(baseURL), "", query)
}

func streamDiscoveredBuildLogs(ctx context.Context, client *api.Client, discoveryPath string, format output.Format, out io.Writer) error {
	var payload gen.BuildLogsUrl
	if err := client.Do(ctx, http.MethodGet, discoveryPath, nil, nil, &payload); err != nil {
		return err
	}
	baseURL, err := validatedBuildLogBaseURL(discoveryPath, payload.Url)
	if err != nil {
		return err
	}
	query := url.Values{"format": {"text"}, "follow": {"true"}}
	resp, err := client.WithBaseURL(baseURL).StreamNoTimeout(ctx, http.MethodGet, "", query, nil, "")
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	var stream interface {
		io.Writer
		Flush() error
	}
	if format == output.FormatJSON {
		stream = output.NewNDJSONDataWriter(out)
	} else {
		stream = output.NewStreamSanitizer(out)
	}
	if _, err := io.Copy(stream, resp.Body); err != nil {
		if ctx.Err() != nil {
			return cerr.New(cerr.ExitInterrupted, "interrupted")
		}
		return err
	}
	return stream.Flush()
}

func printCreatedImage(ctx context.Context, a *app.App, client *api.Client, raw api.Raw[gen.AsyncOperation]) error {
	created := raw.V
	if a.Globals.NoWait {
		return a.Printer().Object(raw, [][2]string{{"id", created.Id}, {"status", created.Status}})
	}
	timeout, err := workload.ParseTimeout(a.Globals.Timeout)
	if err != nil {
		return err
	}
	image, err := workload.WaitForImage(ctx, client, created.Id, timeout)
	if err != nil {
		return err
	}
	return a.Printer().Object(image, imageKV(image.V))
}
