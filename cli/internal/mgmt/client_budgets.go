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

func projectBudgetPath(projectID string) string {
	return "/projects/" + url.PathEscape(projectID) + "/budget"
}

// GetProjectBudget reads GET /projects/{id}/budget. A project without a
// budget answers 200 with null monthly_limit_cents and created_at; a 404
// means the project was not found (a caller without access gets 403).
func GetProjectBudget(ctx context.Context, c *api.Client, projectID string) (*api.Raw[gen.ProjectBudget], error) {
	var resp api.Raw[gen.ProjectBudget]
	if err := c.Do(ctx, http.MethodGet, projectBudgetPath(projectID), nil, nil, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// SetProjectBudget creates or replaces the budget with PUT
// /projects/{id}/budget.
func SetProjectBudget(ctx context.Context, c *api.Client, projectID string, monthlyLimitCents int64) (*api.Raw[gen.ProjectBudget], error) {
	var resp api.Raw[gen.ProjectBudget]
	if err := c.Do(ctx, http.MethodPut, projectBudgetPath(projectID), nil, gen.SetProjectBudgetRequest{MonthlyLimitCents: monthlyLimitCents}, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// RemoveProjectBudget deletes the budget with DELETE /projects/{id}/budget.
func RemoveProjectBudget(ctx context.Context, c *api.Client, projectID string) error {
	return c.Do(ctx, http.MethodDelete, projectBudgetPath(projectID), nil, nil, nil)
}
