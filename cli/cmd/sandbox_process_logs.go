// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"strconv"
	"time"

	"github.com/spf13/cobra"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
)

func newSandboxProcessLogsCommand() *cobra.Command {
	var cursor string
	var encoding string
	var limit int
	var follow bool
	cmd := &cobra.Command{
		Use:   "logs <sandbox-id> <process-id>",
		Short: "Read process logs",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			if follow && a.Format == output.FormatYAML {
				return cerr.Usage("--follow only supports table or json output")
			}
			if encoding != "text" && encoding != "base64" {
				return cerr.Usage("--encoding must be text or base64, got %q", encoding)
			}
			if limit < 0 {
				return cerr.Usage("--limit must not be negative")
			}
			toolClient, _, _, err := sandboxToolboxClient(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			if follow {
				return followSandboxProcessLogs(cmd, a, toolClient, args[1], cursor, encoding, limit)
			}
			return readSandboxProcessLogs(cmd, a, toolClient, args[1], cursor, encoding, limit)
		},
	}
	cmd.Flags().StringVar(&cursor, "cursor", "", "resume from this cursor")
	cmd.Flags().StringVar(&encoding, "encoding", "text", "log encoding: text|base64")
	cmd.Flags().IntVar(&limit, "limit", 0, "max frames to return")
	cmd.Flags().BoolVar(&follow, "follow", false, "follow the log stream with SSE")
	addTimeoutFlag(cmd, defaultWaitTimeout, "with --follow, how long to follow the log (Go duration)")
	return cmd
}

func followSandboxProcessLogs(cmd *cobra.Command, a *app.App, toolClient *toolboxclient.Client, processID, cursor, encoding string, limit int) error {
	timeout, err := sandboxTimeout(a)
	if err != nil {
		return err
	}
	followCtx, cancel := newFollowProcessContext(cmd.Context(), timeout)
	defer cancel()
	toolClient.OnLogWarning = func(w toolboxclient.LogWarning) {
		a.Info("warning: log frames were lost (cursor %s expired); resumed from %s%s", output.SanitizeLine(w.Cursor), output.SanitizeLine(w.FirstAvailableCursor), warningDetail(w.Message))
	}
	if a.Format == output.FormatJSON {
		return followSandboxProcessLogsNDJSON(followCtx, a, toolClient, processID, cursor, encoding, limit, timeout)
	}
	stdoutWriter, stderrWriter, flushLogs := processLogWriters(a)
	result, _, eof, err := toolClient.FollowProcessLogs(followCtx, processID, cursor, logEncodingOrDefault(encoding), limit, stdoutWriter, stderrWriter)
	if flushErr := flushLogs(); flushErr != nil {
		return flushErr
	}
	if err := mapFollowProcessError(followCtx, timeout, eof, err); err != nil {
		return err
	}
	return exitError(result)
}

func followSandboxProcessLogsNDJSON(ctx context.Context, a *app.App, toolClient *toolboxclient.Client, processID, cursor, encoding string, limit int, timeout time.Duration) error {
	encoder := json.NewEncoder(a.Stdout)
	encoder.SetEscapeHTML(false)
	result, _, eof, err := toolClient.FollowProcessLogFrames(ctx, processID, cursor, logEncodingOrDefault(encoding), limit, func(frame api.Raw[tbgen.LogFrameDto]) error {
		converted, err := output.SnakeCase(frame)
		if err != nil {
			return err
		}
		return encoder.Encode(converted)
	})
	if err := mapFollowProcessError(ctx, timeout, eof, err); err != nil {
		return err
	}
	return exitError(result)
}

func readSandboxProcessLogs(cmd *cobra.Command, a *app.App, toolClient *toolboxclient.Client, processID string, cursor string, encoding string, limit int) error {
	query := url.Values{}
	if cursor != "" {
		query.Set("cursor", cursor)
	}
	if limit > 0 {
		query.Set("limit", strconv.Itoa(limit))
	}
	if encoding != "" {
		query.Set("encoding", encoding)
	}
	page, err := toolClient.ReadProcessLogs(cmd.Context(), processID, query)
	if err != nil {
		return err
	}
	if a.Format == output.FormatTable {
		if page.V.TruncatedHead {
			// Retention evicted frames before this page: say so, or an
			// expired cursor reads like a complete log.
			first := ""
			if page.V.FirstAvailableCursor != nil {
				first = " (first surviving cursor " + output.SanitizeLine(*page.V.FirstAvailableCursor) + ")"
			}
			a.Info("warning: older log frames were evicted by retention; this page is not the beginning of the log%s", first)
		}
		stdoutWriter, stderrWriter, flushLogs := processLogWriters(a)
		if err := toolboxclient.WriteLogFrames(page.V.Frames, logEncodingOrDefault(encoding), stdoutWriter, stderrWriter); err != nil {
			return err
		}
		return flushLogs()
	}
	return printToolboxObject(a, page, nil)
}

func processLogWriters(a *app.App) (io.Writer, io.Writer, func() error) {
	if a.Format != output.FormatTable {
		return a.Stdout, a.Stderr, func() error { return nil }
	}
	stdoutStream := output.NewStreamSanitizer(a.Stdout)
	stderrStream := output.NewStreamSanitizer(a.Stderr)
	return stdoutStream, stderrStream, func() error {
		if err := stdoutStream.Flush(); err != nil {
			return err
		}
		return stderrStream.Flush()
	}
}

func newFollowProcessContext(ctx context.Context, timeout time.Duration) (context.Context, context.CancelFunc) {
	if timeout <= 0 {
		return ctx, func() {}
	}
	return context.WithTimeout(ctx, timeout)
}

func mapFollowProcessError(ctx context.Context, timeout time.Duration, eof bool, err error) error {
	if timeout > 0 && (errors.Is(ctx.Err(), context.DeadlineExceeded) || errors.Is(err, context.DeadlineExceeded)) {
		return cerr.New(cerr.ExitTimeout, "timed out after %s waiting for the process to finish", timeout)
	}
	if errors.Is(ctx.Err(), context.Canceled) || errors.Is(err, context.Canceled) {
		return cerr.New(cerr.ExitInterrupted, "interrupted")
	}
	if err != nil {
		return err
	}
	if !eof {
		return toolboxclient.ErrProcessLogsStreamEndedEarly
	}
	return nil
}

func warningDetail(message string) string {
	if message == "" {
		return ""
	}
	return ": " + output.SanitizeLine(message)
}
