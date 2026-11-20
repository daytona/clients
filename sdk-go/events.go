// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"os"
	"strings"

	"go.daytona.com/sdk/v2/internal/events"
)

type eventsStateSource struct {
	hub *events.Hub[stateObservation]
}

func (c *Client) installSandboxEvents() {
	config := c.config.events
	if config.Enabled != nil && !*config.Enabled {
		return
	}
	idle := config.IdleClose
	if idle == NoTimeout {
		idle = 0
	}
	// Go sockets never pin process exit, so the default keeps them warm.
	c.stateSource = &eventsStateSource{hub: events.New(c.ctx, events.Config[stateObservation]{
		Connect: c.connectSandboxEvents, ReconcileInterval: config.ReconcileInterval, IdleClose: idle,
		Observe: func(update events.Update) stateObservation {
			return stateObservation{
				State: sandboxState(update.Status), RawState: update.Status, Reason: update.Reason, At: update.At,
				NotFound: update.NotFound,
			}
		},
	})}
}

func (c *Client) closeSandboxEvents() {
	if source, ok := c.stateSource.(*eventsStateSource); ok {
		source.hub.Close()
	}
}

func (s *eventsStateSource) watch(ctx context.Context, id, region string) (<-chan stateObservation, func()) {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("DAYTONA_SANDBOX_EVENTS"))) {
	case "0", "false", "off":
		return nil, func() {}
	default:
	}
	if tag := regionOf(id); tag != "" {
		region = tag
	}
	if strings.TrimSpace(region) == "" || strings.EqualFold(strings.TrimSpace(region), "local") {
		region = "_"
	}
	if _, err := pathSegment(region); err != nil {
		return nil, func() {}
	}
	return s.hub.Watch(ctx, id, region)
}
