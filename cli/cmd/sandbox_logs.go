// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
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
		Long:  "Mint a preview URL for a sandbox port. Unless the port was exposed with --public, the URL needs the printed token in the X-Daytona-Preview-Token header, which a browser cannot send. For a URL that opens in a browser as-is, run `daytona sandbox port expose <sandbox-id> <port> --public`. The sandbox daemon's ports 2280, 2281 and 22220 are never preview ports and are refused.\nExamples:\n  daytona sandbox port preview-url <sandbox-id> 8080\n  daytona sandbox port preview-url <sandbox-id> 8080 --signed --ttl 5m",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			if cmd.Flags().Changed("ttl") && !signed {
				return cerr.Usage("--ttl requires --signed")
			}
			port, err := parsePreviewPortArg(args[1])
			if err != nil {
				return err
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
			path := fmt.Sprintf("/sandboxes/%s/ports/%d/preview-url", url.PathEscape(resolved.Detail.Id), port)
			if signed {
				path = fmt.Sprintf("/sandboxes/%s/ports/%d/signed-preview-urls", url.PathEscape(resolved.Detail.Id), port)
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
			if err := a.Printer().Object(resp, [][2]string{{"url", resp.V.Url}, {"token", resp.V.Token}}); err != nil {
				return err
			}
			if resp.V.Token != "" {
				a.Info("note: send the token in the X-Daytona-Preview-Token header; for a URL that opens in a browser with no credential, run `daytona sandbox port expose %s %d --public`", resolved.Detail.Id, port)
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&signed, "signed", false, "mint a short-lived signed preview URL")
	addSecondsFlag(cmd.Flags(), &ttl, "ttl", "60s", "lifetime of a --signed preview URL (Go duration)")
	cmd.AddCommand(newSandboxPreviewURLExpireCommand())
	return cmd
}

func newSandboxPortExposeCommand() *cobra.Command {
	public := false
	cmd := &cobra.Command{
		Use:   "expose <sandbox-id> <port>",
		Short: "Expose a sandbox port as a preview URL, privately or publicly",
		Long:  "Expose a sandbox port as a preview URL (PUT /sandboxes/{id}/ports/{port}). The SDK twin is sandbox.ports.expose(port, public=…).\nPorts are private by default: a private URL needs the token from `daytona sandbox port preview-url` in the X-Daytona-Preview-Token header. With --public the URL needs no credential and opens in a browser as-is. Exposing an exposed port again sets its visibility, so running it without --public makes a public port private. The sandbox daemon's ports 2280, 2281 and 22220 are never preview ports and are refused.\nExamples:\n  daytona sandbox port expose <sandbox-id> 8080 --public\n  daytona sandbox port expose <sandbox-id> 8080",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			port, err := parsePreviewPortArg(args[1])
			if err != nil {
				return err
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
			path := fmt.Sprintf("/sandboxes/%s/ports/%d", url.PathEscape(resolved.Detail.Id), port)
			var resp api.Raw[gen.SandboxPort]
			if err := resolved.Client.Do(cmd.Context(), http.MethodPut, path, nil, gen.SetSandboxPortRequest{Public: public}, &resp); err != nil {
				return err
			}
			return a.Printer().Object(resp, [][2]string{{"port", strconv.Itoa(int(resp.V.Port))}, {"public", strconv.FormatBool(resp.V.Public)}, {"url", resp.V.Url}})
		},
	}
	cmd.Flags().BoolVar(&public, "public", false, "make the preview URL reachable without any credential")
	return cmd
}

func newSandboxPortUnexposeCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "unexpose <sandbox-id> <port>",
		Short: "Remove a sandbox port's preview URL",
		Long:  "Remove a sandbox port's preview URL (DELETE /sandboxes/{id}/ports/{port}); it also works while the sandbox is being deleted. The SDK twin is sandbox.ports.unexpose(port).\nExample:\n  daytona sandbox port unexpose <sandbox-id> 8080",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			port, err := parsePortArg(args[1])
			if err != nil {
				return err
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
			path := fmt.Sprintf("/sandboxes/%s/ports/%d", url.PathEscape(resolved.Detail.Id), port)
			if err := resolved.Client.Do(cmd.Context(), http.MethodDelete, path, nil, nil, nil); err != nil {
				return err
			}
			return a.Printer().Raw("unexposed", map[string]any{"sandbox_id": resolved.Detail.Id, "port": port, "unexposed": true})
		},
	}
}

func newSandboxPreviewURLExpireCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "expire <sandbox-id> <port> <token>",
		Short: "Expire one signed preview URL",
		Long:  "Expire one signed preview URL. The token is not printed back.\nExample:\n  daytona sandbox port preview-url expire <sandbox-id> 8080 <token>",
		Args:  cobra.ExactArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			port, err := parsePortArg(args[1])
			if err != nil {
				return err
			}
			if strings.TrimSpace(args[2]) == "" {
				return cerr.Usage("the signed preview URL token must not be empty")
			}
			a := FromContext(cmd.Context())
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			resolved, err := workload.ResolveSandbox(cmd.Context(), client, workload.NewCatalogCache(), args[0])
			if err != nil {
				return err
			}
			path := fmt.Sprintf("/sandboxes/%s/ports/%d/signed-preview-urls/revoke", url.PathEscape(resolved.Detail.Id), port)
			body := gen.RevokeSandboxSignedPreviewUrlRequest{Token: args[2]}
			if err := resolved.Client.Do(cmd.Context(), http.MethodPost, path, nil, body, nil); err != nil {
				return redactInError(err, args[2])
			}
			return a.Printer().Raw("revoked", map[string]any{"sandbox_id": resolved.Detail.Id, "port": port, "revoked": true})
		},
	}
}

// redactInError keeps err's exit code and details but drops secret from its
// message, should the server echo it.
func redactInError(err error, secret string) error {
	if secret == "" || !strings.Contains(err.Error(), secret) {
		return err
	}
	out := &cerr.Error{Code: cerr.ExitCode(err), Msg: strings.ReplaceAll(err.Error(), secret, "[redacted]"), Err: err}
	var ce *cerr.Error
	if errors.As(err, &ce) {
		out.Hint, out.Source = strings.ReplaceAll(ce.Hint, secret, "[redacted]"), ce.Source
	}
	return out
}

// reservedPreviewPorts are the sandbox daemon's own ports: the API never
// makes them preview ports.
var reservedPreviewPorts = map[int]bool{2280: true, 2281: true, 22220: true}

// parsePreviewPortArg is parsePortArg for a port a preview URL is minted for.
func parsePreviewPortArg(raw string) (int, error) {
	port, err := parsePortArg(raw)
	if err != nil {
		return 0, err
	}
	if reservedPreviewPorts[port] {
		return 0, cerr.Usage("port %d is reserved for the sandbox daemon and is never a preview port", port)
	}
	return port, nil
}

// parsePortArg checks a <port> argument (1-65535) before any request.
func parsePortArg(raw string) (int, error) {
	port, err := strconv.Atoi(raw)
	if err != nil || port < 1 || port > 65535 {
		return 0, cerr.Usage("invalid port %q: want 1-65535", raw)
	}
	return port, nil
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
