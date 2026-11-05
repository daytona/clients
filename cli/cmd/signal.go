// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"os"
	"os/signal"
	"syscall"
)

// signalContext is cancelled by the first SIGINT/SIGTERM. Once it is, the
// default signal behavior is restored, so a second Ctrl+C terminates a
// command that is stuck after the cancellation.
func signalContext() (context.Context, context.CancelFunc) {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	context.AfterFunc(ctx, stop)
	return ctx, stop
}
