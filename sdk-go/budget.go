// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"time"
)

// ProjectBudget describes the project's monthly spend at list price, before
// credits. Spend observations can lag usage by several minutes.
type ProjectBudget struct {
	// MonthlyLimitCents is the monthly limit in cents; nil means unset.
	MonthlyLimitCents *int64
	// PeriodStartDate is the period's calendar date at UTC midnight, or zero.
	PeriodStartDate time.Time
	// SpentCents is the observed spend in cents; nil means unreported.
	SpentCents *int64
	// CreatedAt is zero when the budget has no reported creation timestamp.
	CreatedAt time.Time
	// UpdatedAt is zero when the budget has no reported update timestamp.
	UpdatedAt time.Time
	// EvaluatedAt is zero when no evaluation was reported.
	EvaluatedAt time.Time
	// ExceededAt is zero when no budget exceedance was reported.
	ExceededAt time.Time
	// StopAt is zero when no scheduled stop was reported.
	StopAt time.Time
}

// ProjectBudget returns the API key project's monthly budget.
// The key needs read:spending. An unset budget has nil MonthlyLimitCents and
// zero PeriodStartDate. Malformed responses return ErrBadRequest.
// While over budget, sandbox creation/start can return ErrProjectBudgetExceeded.
func (c *Client) ProjectBudget(ctx context.Context) (*ProjectBudget, error) {
	var raw json.RawMessage
	if err := c.api().do(ctx, apiRequest{Path: "/usage/budget", Result: &raw}); err != nil {
		return nil, err
	}
	object := accountResponse(raw, "project budget")
	for _, name := range []string{"period_start_date", "created_at", "updated_at"} {
		object.nullable(name)
	}
	result := &ProjectBudget{
		MonthlyLimitCents: object.optionalInteger("monthly_limit_cents", true),
		SpentCents:        object.optionalInteger("spent_cents", false),
		CreatedAt:         object.timestamp("created_at", false),
		UpdatedAt:         object.timestamp("updated_at", false),
		EvaluatedAt:       object.timestamp("evaluated_at", false),
		ExceededAt:        object.timestamp("exceeded_at", false),
		StopAt:            object.timestamp("stop_at", false),
	}
	if present(object.fields["period_start_date"]) {
		date := object.text("period_start_date", true)
		parsed, err := time.Parse(time.DateOnly, date)
		if err != nil || parsed.Year() < 1 {
			object.fail("period_start_date", badRequest("expected a YYYY-MM-DD calendar date"))
		}
		result.PeriodStartDate = parsed
	}
	if *object.err != nil {
		return nil, *object.err
	}
	return result, nil
}
