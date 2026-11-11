// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"os"
	"os/signal"
	"syscall"

	"github.com/daytona/clients/cli/internal/cerr"
)

// errSignalled is the cancellation cause of the invocation's context when a
// signal ended it; Signal is the signal received.
type errSignalled struct{ Signal os.Signal }

func (e errSignalled) Error() string { return "interrupted by " + e.Signal.String() }

// signalContext is cancelled by the first SIGINT/SIGTERM, with an errSignalled
// cause naming it. Once it is, the default signal behavior is restored, so a
// second Ctrl+C terminates a command that is stuck after the cancellation.
func signalContext() (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancelCause(context.Background())
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM)
	go func() {
		select {
		case sig := <-signals:
			signal.Stop(signals)
			cancel(errSignalled{Signal: sig})
		case <-ctx.Done():
			signal.Stop(signals)
		}
	}()
	return ctx, func() { cancel(context.Canceled) }
}

// interruptedExitCode is the exit code of an interrupted invocation: 128 plus
// the signal number for SIGTERM (143), as a shell reports it, and 130 for
// Ctrl-C or a declined confirmation.
func interruptedExitCode(ctx context.Context, code int) int {
	if code != cerr.ExitInterrupted {
		return code
	}
	var signalled errSignalled
	if errors.As(context.Cause(ctx), &signalled) && signalled.Signal == syscall.SIGTERM {
		return cerr.ExitTerminated
	}
	return code
}
