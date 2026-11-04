// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package mgmt

import (
	"context"
	"net/http"
	"net/url"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
)

func ListAuditLogs(ctx context.Context, c *api.Client, orgID string, query url.Values) (*gen.AuditLogPage, error) {
	var resp gen.AuditLogPage
	if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID)+"/audit-logs", query, nil, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}
