// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"time"

	"go.daytona.com/sdk/v2/internal/events"
)

type stateObservation struct {
	State    SandboxState
	RawState string
	Reason   string
	At       time.Time
	// NotFound is a terminal disappearance, not a sandbox state observation.
	NotFound bool
}

// watch's release is idempotent. A nil channel means no event source is
// available: lifecycle code must use API reads. The hub owns channel closure.
// A non-nil channel requires liveness with non-nil Frame and Jitter functions.
type sandboxStateSource interface {
	watch(ctx context.Context, sandboxID, region string) (<-chan stateObservation, func(), events.WatchLiveness)
}

type noopStateSource struct{}

func (noopStateSource) watch(context.Context, string, string) (<-chan stateObservation, func(), events.WatchLiveness) {
	return nil, func() {}, events.WatchLiveness{}
}
