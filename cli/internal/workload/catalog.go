// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package workload

import (
	"context"
	"sync"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/regions"
)

type CatalogCache struct {
	once sync.Once
	cat  *regions.Catalog
	err  error
}

func NewCatalogCache() *CatalogCache {
	return &CatalogCache{}
}

func (c *CatalogCache) Fetch(ctx context.Context, home *api.Client) (*regions.Catalog, error) {
	c.once.Do(func() {
		c.cat, c.err = regions.Fetch(ctx, home)
	})
	return c.cat, c.err
}
