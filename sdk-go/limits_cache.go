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
	lookup := cache.lookup
	if lookup == nil {
		c.workerMu.Lock()
		if err := c.ensureOpen(); err != nil {
			c.workerMu.Unlock()
			cache.mu.Unlock()
			return "", err
		}
		lookup = &accountOrganizationLookup{done: make(chan struct{})}
		cache.lookup = lookup
		c.workers.Add(1)
		go c.lookupAccountOrganization(context.WithoutCancel(ctx), lookup)
		c.workerMu.Unlock()
	}
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

func (c *Client) lookupAccountOrganization(ctx context.Context, lookup *accountOrganizationLookup) {
	defer c.workers.Done()
	// The transport bounds this caller-independent lookup by Client.Close and
	// the request timeout, while preserving the initiating context's values.
	key, err := c.CurrentAPIKey(ctx)
	if err == nil {
		lookup.orgID = key.Context.OrgID
		_, err = pathSegment(lookup.orgID)
	}
	lookup.err = err
	cache := &c.accountLimits
	cache.mu.Lock()
	defer cache.mu.Unlock()
	if err != nil {
		// Failed or malformed lookups do not poison subsequent calls.
		cache.lookup = nil
	}
	close(lookup.done)
}
