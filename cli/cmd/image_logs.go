// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

func newImageLogsCommand() *cobra.Command {
	follow := false
	cmd := &cobra.Command{
		Use:   "logs <id-or-name>",
		Short: "Read image build logs",
		Long: "Read image build logs by name or id. With --follow, table output streams the log text as it arrives;\n" +
			"-o json streams NDJSON, one {\"data\": \"...\"} object per chunk; -o yaml is not supported.\n" +
			"Example:\n  daytona image logs <id-or-name> --follow",
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
				// The timeout covers name resolution, discovery and streaming.
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

func discoverImageBuildLogs(ctx context.Context, client *api.Client, discoveryPath string) (string, error) {
	// Resolve the reference before comparing identities: the proxy URL holds
	// the immutable id, not the name the caller may have supplied.
	var image gen.Image
	if err := client.Do(ctx, http.MethodGet, strings.TrimSuffix(discoveryPath, "/build-logs-url"), nil, nil, &image); err != nil {
		return "", err
	}
	if _, ok := buildLogPathSegment(url.PathEscape(image.Id)); !ok {
		return "", cerr.New(cerr.ExitFailure, "invalid image response: missing or invalid id")
	}
	resolvedPath := "/images/" + url.PathEscape(image.Id) + "/build-logs-url"
	var payload gen.BuildLogsUrl
	if err := client.Do(ctx, http.MethodGet, resolvedPath, nil, nil, &payload); err != nil {
		return "", err
	}
	return validatedBuildLogBaseURL(resolvedPath, payload.Url)
}

func readDiscoveredBuildLogs(ctx context.Context, client *api.Client, discoveryPath string, follow bool) (string, error) {
	baseURL, err := discoverImageBuildLogs(ctx, client, discoveryPath)
	if err != nil {
		return "", err
	}
	query := url.Values{"format": {"text"}, "follow": {"false"}}
	if follow {
		query.Set("follow", "true")
	}
	resp, err := client.WithBaseURL(baseURL).Stream(ctx, http.MethodGet, "", query, nil, "")
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	const maxBuildLogBytes = 64 << 20
	if resp.ContentLength > maxBuildLogBytes {
		return "", cerr.New(cerr.ExitFailure, "image build log exceeds the 64 MiB read limit; use --follow to stream it")
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxBuildLogBytes+1))
	if err != nil {
		return "", err
	}
	if len(body) > maxBuildLogBytes {
		return "", cerr.New(cerr.ExitFailure, "image build log exceeds the 64 MiB read limit; use --follow to stream it")
	}
	return string(body), nil
}

func streamDiscoveredBuildLogs(ctx context.Context, client *api.Client, discoveryPath string, format output.Format, out io.Writer) error {
	baseURL, err := discoverImageBuildLogs(ctx, client, discoveryPath)
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
