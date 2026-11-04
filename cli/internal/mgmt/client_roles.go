// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package mgmt

import (
	"context"
	"net/http"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
)

func ListRoles(ctx context.Context, c *api.Client) (*gen.RoleList, error) {
	var resp gen.RoleList
	if err := c.Do(ctx, http.MethodGet, "/roles", nil, nil, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}
