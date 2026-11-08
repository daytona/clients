// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

func newSandboxLogsCommand() *cobra.Command {
	entrypoint := false
	follow := false
	cmd := &cobra.Command{
		Use:   "logs <id>",
		Short: "Read sandbox logs",
		Long:  "Read sandbox logs.\nExamples:\n  daytona sandbox logs <id>\n  daytona sandbox logs <id> --entrypoint",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			if follow && a.Format == output.FormatYAML {
				return cerr.Usage("--output yaml is not supported with --follow")
			}
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			resolved, err := workload.ResolveSandbox(cmd.Context(), home, workload.NewCatalogCache(), args[0])
			if err != nil {
				return err
			}
			path, err := sandboxLogsPath(resolved.Detail, entrypoint)
			if err != nil {
				return err
			}
			text, err := readSandboxLogs(cmd, resolved.Client, path, follow, a.Format, cmd.OutOrStdout(), cmd.ErrOrStderr())
			if err != nil {
				return err
			}
			if follow {
				return nil
			}
			return a.Printer().Raw(text, map[string]any{"logs": text})
		},
	}
	cmd.Flags().BoolVar(&entrypoint, "entrypoint", false, "read entrypoint logs")
	cmd.Flags().BoolVar(&follow, "follow", false, "poll for new log bytes until interrupted")
	return cmd
}

func newSandboxUsageCommand() *cobra.Command {
	history := false
	cmd := &cobra.Command{
		Use:   "usage <id>",
		Short: "Read sandbox usage",
		Long:  "Read sandbox usage.\nExamples:\n  daytona sandbox usage <id>\n  daytona sandbox usage <id> --history",
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
			if history && resolved.Detail.Class != workload.ClassLinux {
				return workload.LinuxOnlyError("usage --history", resolved.Detail.Id, string(resolved.Detail.Class))
			}
			path := fmt.Sprintf("/sandboxes/%s/usage", url.PathEscape(resolved.Detail.Id))
			if history {
				path += "/history"
			}
			if history {
				var resp json.RawMessage
				if err := resolved.Client.Do(cmd.Context(), http.MethodGet, path, nil, nil, &resp); err != nil {
					return err
				}
				return a.Printer().Object(resp, [][2]string{{"id", resolved.Detail.Id}, {"history", fmt.Sprintf("%t", history)}})
			}
			var usageResp api.Raw[gen.SandboxUsage]
			if err := resolved.Client.Do(cmd.Context(), http.MethodGet, path, nil, nil, &usageResp); err != nil {
				return err
			}
			return a.Printer().Object(usageResp, sandboxUsageKV(&usageResp.V))
		},
	}
	cmd.Flags().BoolVar(&history, "history", false, "read usage history")
	return cmd
}

func newSandboxPreviewURLCommand() *cobra.Command {
	signed := false
	ttl := secondsFlag{seconds: 60}
	cmd := &cobra.Command{
		Use:   "preview-url <sandbox-id> <port>",
		Short: "Mint a preview URL for a sandbox port",
		Long:  "Mint a preview URL for a sandbox port.\nExamples:\n  daytona sandbox port preview-url <sandbox-id> 8080\n  daytona sandbox port preview-url <sandbox-id> 8080 --signed --ttl 5m",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			if cmd.Flags().Changed("ttl") && !signed {
				return cerr.Usage("--ttl requires --signed")
			}
			a := FromContext(cmd.Context())
			home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			resolved, err := workload.ResolveSandbox(cmd.Context(), home, workload.NewCatalogCache(), args[0])
			if err != nil {
				return err
			}
			path := fmt.Sprintf("/sandboxes/%s/ports/%s/preview-url", url.PathEscape(resolved.Detail.Id), url.PathEscape(args[1]))
			if signed {
				path = fmt.Sprintf("/sandboxes/%s/ports/%s/signed-preview-urls", url.PathEscape(resolved.Detail.Id), url.PathEscape(args[1]))
				body := gen.CreateSandboxSignedPreviewUrlRequest{ExpiresInSeconds: &ttl.seconds}
				var resp api.Raw[gen.SignedPreviewUrl]
				if err := resolved.Client.Do(cmd.Context(), http.MethodPost, path, nil, body, &resp); err != nil {
					return err
				}
				return a.Printer().Object(resp, [][2]string{{"url", resp.V.Url}, {"token", resp.V.Token}, {"expires_at", resp.V.ExpiresAt.Format(time.RFC3339)}})
			}
			var resp api.Raw[gen.PreviewUrl]
			if err := resolved.Client.Do(cmd.Context(), http.MethodPost, path, nil, nil, &resp); err != nil {
				return err
			}
			return a.Printer().Object(resp, [][2]string{{"url", resp.V.Url}, {"token", resp.V.Token}})
		},
	}
	cmd.Flags().BoolVar(&signed, "signed", false, "mint a short-lived signed preview URL")
	addSecondsFlag(cmd.Flags(), &ttl, "ttl", "60s", "lifetime of a --signed preview URL (Go duration)")
	cmd.AddCommand(newSandboxPreviewURLExpireCommand())
	return cmd
}

func newSandboxPreviewURLExpireCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "expire <sandbox-id> <port> <token>",
		Short: "Expire one signed preview URL",
		Long:  "Expire one signed preview URL.\nExample:\n  daytona sandbox port preview-url expire <sandbox-id> 8080 <token>",
		Args:  cobra.ExactArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			resolved, err := workload.ResolveSandbox(cmd.Context(), client, workload.NewCatalogCache(), args[0])
			if err != nil {
				return err
			}
			path := fmt.Sprintf("/sandboxes/%s/ports/%s/signed-preview-urls/revoke", url.PathEscape(resolved.Detail.Id), url.PathEscape(args[1]))
			body := gen.RevokeSandboxSignedPreviewUrlRequest{Token: args[2]}
			if err := resolved.Client.Do(cmd.Context(), http.MethodPost, path, nil, body, nil); err != nil {
				return err
			}
			return a.Printer().Raw("revoked", map[string]any{"sandbox_id": resolved.Detail.Id, "port": args[1], "token": args[2], "revoked": true})
		},
	}
}

func sandboxLogsPath(detail gen.Sandbox, entrypoint bool) (string, error) {
	switch detail.Class {
	case workload.ClassLinux, workload.ClassMacOS:
		if entrypoint {
			return fmt.Sprintf("/sandboxes/%s/logs", url.PathEscape(detail.Id)), nil
		}
		return fmt.Sprintf("/sandboxes/%s/console-logs", url.PathEscape(detail.Id)), nil
	default:
		return "", cerr.New(cerr.ExitConflict, "logs are not available for sandbox class %s", detail.Class)
	}
}

var sandboxLogsPollInterval = 2 * time.Second
var sandboxLogsPollIntervalMax = 10 * time.Second

func readSandboxLogs(cmd *cobra.Command, client *api.Client, path string, follow bool, format output.Format, out io.Writer, errOut io.Writer) (string, error) {
	text, err := readText(cmd.Context(), client, path, nil)
	if err != nil || !follow {
		return text, err
	}
	writeChunk, err := sandboxLogChunkWriter(format, out)
	if err != nil {
		return "", err
	}
	if err := writeChunk(text); err != nil {
		return "", err
	}
	seen := text
	interval := sandboxLogsPollInterval
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-cmd.Context().Done():
			return "", cerr.New(cerr.ExitInterrupted, "interrupted")
		case <-ticker.C:
			// Follow fetches fresh log snapshots and emits only newly seen bytes.
			current, currentErr := readText(cmd.Context(), client, path, nil)
			if currentErr != nil {
				return "", currentErr
			}
			chunk, truncated := sandboxLogDelta(seen, current)
			if truncated {
				if _, writeErr := errOut.Write([]byte("warning: log output was truncated between polls; resuming from the current retained window\n")); writeErr != nil {
					return "", writeErr
				}
			}
			if chunk != "" {
				if err := writeChunk(chunk); err != nil {
					return "", err
				}
			}
			interval = nextSandboxLogsPollInterval(interval, current != seen)
			ticker.Reset(interval)
			seen = current
		}
	}
}

func sandboxLogChunkWriter(format output.Format, out io.Writer) (func(string) error, error) {
	switch format {
	case output.FormatJSON:
		enc := json.NewEncoder(out)
		enc.SetEscapeHTML(false)
		return func(chunk string) error {
			return enc.Encode(map[string]string{"data": chunk})
		}, nil
	case output.FormatYAML:
		return nil, cerr.Usage("--output yaml is not supported with --follow")
	default:
		stream := output.NewStreamSanitizer(out)
		return func(chunk string) error {
			_, err := stream.Write([]byte(chunk))
			return err
		}, nil
	}
}

func nextSandboxLogsPollInterval(current time.Duration, changed bool) time.Duration {
	if changed {
		return sandboxLogsPollInterval
	}
	if current < 5*time.Second {
		return 5 * time.Second
	}
	return sandboxLogsPollIntervalMax
}

func sandboxUsageKV(resp *gen.SandboxUsage) [][2]string {
	return [][2]string{{"sandbox_id", resp.SandboxId}, {"usage", jsonCell(resp.Usage)}}
}

func readText(ctx context.Context, client *api.Client, path string, query url.Values) (string, error) {
	resp, err := client.Stream(ctx, http.MethodGet, path, query, nil, "")
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	return string(body), nil
}
