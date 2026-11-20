// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"sync"
)

type accountLimitsCache struct {
	mu     sync.Mutex
	lookup *accountOrganizationLookup
}

type accountOrganizationLookup struct {
	done  chan struct{}
	orgID string
	err   error
}

func (c *Client) accountOrganizationID(ctx context.Context) (string, error) {
	if c.config.apiKey == nil {
		return "", badRequest("OrganizationID is required with JWT authentication")
	}
	cache := &c.accountLimits
	cache.mu.Lock()
	if lookup := cache.lookup; lookup != nil {
		cache.mu.Unlock()
		select {
		case <-ctx.Done():
			return "", transportError(ctx, ctx.Err())
		case <-c.ctx.Done():
			return "", closedError()
		case <-lookup.done:
			return lookup.orgID, lookup.err
		}
	}
	lookup := &accountOrganizationLookup{done: make(chan struct{})}
	cache.lookup = lookup
	cache.mu.Unlock()

	key, err := c.CurrentAPIKey(ctx)
	if err == nil {
		lookup.orgID = key.Context.OrgID
	}
	lookup.err = err
	cache.mu.Lock()
	if err != nil {
		// Failed lookups do not poison subsequent calls.
		cache.lookup = nil
	}
	close(lookup.done)
	cache.mu.Unlock()
	return lookup.orgID, lookup.err
}
