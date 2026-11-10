// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"errors"
	"fmt"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/regions"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

func printCreatedSandbox(cmd *cobra.Command, a *app.App, home *api.Client, cache *workload.CatalogCache, raw api.Raw[gen.SandboxCreateOperation]) error {
	created := raw.V
	if strings.TrimSpace(created.Id) == "" || created.Id == "." || created.Id == ".." {
		return cerr.New(cerr.ExitFailure, "invalid create response: sandbox id must be non-blank and must not be . or ..")
	}
	// The create acknowledgement carries the sandbox's access token: it is
	// never printed. Both modes print the sandbox detail, the shape `sandbox
	// get` prints: --no-wait reads it once, the default waits for running.
	if a.Globals.NoWait {
		resolved, err := workload.ResolveSandbox(cmd.Context(), home, cache, created.Id)
		if err != nil {
			// The create succeeded: print what the acknowledgement says that
			// is also a detail field (never its access token), and warn.
			// Not silenced by --quiet: a billable create succeeded and its
			// detail could not be shown.
			fmt.Fprintln(a.Stderr, output.SanitizeLine(fmt.Sprintf("warning: sandbox %s was created, but reading it failed: %v", created.Id, err)))
			return a.Printer().Object(map[string]any{"id": created.Id, "name": created.Name, "status": created.Status},
				[][2]string{{"id", created.Id}, {"name", created.Name}, {"status", created.Status}})
		}
		return a.Printer().Object(resolved.Raw, sandboxDetailKV(resolved.Detail))
	}
	timeout, err := workload.ParseTimeout(a.Globals.Timeout)
	if err != nil {
		return err
	}
	cat, err := cache.Fetch(cmd.Context(), home)
	if err != nil {
		return createdButUnreadable(created.Id, err)
	}
	client := regions.ClientFor(cmd.Context(), home, cat, created.Id)
	detail, err := workload.WaitForSandbox(cmd.Context(), client, workload.SandboxWaitOptions{
		ID:      created.Id,
		Target:  "running",
		Timeout: timeout,
	})
	if err != nil {
		return createdButUnreadable(created.Id, err)
	}
	return a.Printer().Object(detail, sandboxDetailKV(detail.V))
}

// withRetryKeyHint names the idempotency key when a create may have reached
// the server without an answer (a network error, a 5xx, a timeout or interruption), so a retry
// with the same key replays it instead of creating a second sandbox.
func withRetryKeyHint(err error, key string) error {
	if code := cerr.ExitCode(err); key == "" || (code != cerr.ExitFailure && code != cerr.ExitTimeout && code != cerr.ExitInterrupted) {
		return err
	}
	out := &cerr.Error{Code: cerr.ExitCode(err), Err: err}
	var ce *cerr.Error
	out.Hint = fmt.Sprintf("the sandbox may have been created; retry with --idempotency-key %s to get the same sandbox instead of a second one", key)
	if errors.As(err, &ce) {
		out.Source = ce.Source
		if ce.Hint != "" {
			out.Hint = ce.Hint + "; " + out.Hint
		}
	}
	return out
}

// createdButUnreadable keeps the exit code and details of err but names the
// sandbox the accepted create made, so a failed read or wait never leaves the
// caller without its id.
func createdButUnreadable(id string, err error) error {
	out := &cerr.Error{Code: cerr.ExitCode(err), Msg: fmt.Sprintf("sandbox %s was created, but %v", id, err), Err: err}
	var ce *cerr.Error
	if errors.As(err, &ce) {
		out.Hint, out.Source = ce.Hint, ce.Source
	}
	if out.Hint == "" {
		out.Hint = fmt.Sprintf("check it with `daytona sandbox get %s`; delete it with `daytona sandbox delete %s`", id, id)
	}
	return out
}
