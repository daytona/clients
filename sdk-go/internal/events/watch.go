// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"context"
	"time"

	"github.com/coder/websocket"
)

// FrameStamp identifies the current socket and its most recent incoming frame.
// Compare stamps to detect a frame or socket replacement during a poll.
type FrameStamp struct {
	LastFrameAt time.Time
	connection  *websocket.Conn
}

// WatchLiveness supplies concurrency-safe socket activity for one watch.
// Frame follows the watch when routing moves it to another socket. Jitter
// applies the hub's +/-10% scheduling jitter. Both functions are non-nil for
// an available watch; a watch without an observation channel has zero liveness.
type WatchLiveness struct {
	RegisteredAt time.Time
	Frame        func() FrameStamp
	Jitter       func(time.Duration) time.Duration
}

// Watch subscribes until ctx ends or release is called. Release is idempotent.
// Slow consumers receive the latest observation, not an unbounded event backlog.
func (h *Hub[T]) Watch(ctx context.Context, id, region string) (<-chan T, func()) {
	updates, release, _ := h.WatchWithLiveness(ctx, id, region)
	return updates, release
}
