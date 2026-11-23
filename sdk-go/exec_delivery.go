// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"time"
)

// outputDeliveryError distinguishes caller destinations from protocol and
// transport failures, even when their causes also match context cancellation.
type outputDeliveryError struct{ error }

func (e *outputDeliveryError) Unwrap() error { return e.error }

func (h *ProcessHandle) stopAfterOutputFailure(ctx context.Context, signal ProcessSignal) {
	cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer cancel()
	// Code cells use the same INT operation as interpreter timeout handling.
	h.Kill(cleanupCtx, &KillProcessOptions{Signal: signal}) //nolint:errcheck // Preserve the delivery error.
}
