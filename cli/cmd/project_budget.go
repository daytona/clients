// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
)

// newProjectBudgetCmd is `daytona project budget`: read, set and remove a project's monthly spend budget through the
// project settings routes /projects/{id}/budget.
func newProjectBudgetCmd() *cobra.Command {
	c := &cobra.Command{
		Use:   "budget",
		Short: "A project's monthly spend budget: get, set, remove",
		Long: `A project's monthly spend budget caps its spend in each calendar month (UTC),
at list price and before credits. When spend reaches the budget, new Linux
sandboxes in the project can't start, and after the organization's grace period
running Linux sandboxes are deleted. macOS sandboxes keep running; the budget
instead blocks buying or renewing a Mac reservation for the project.
Spend is evaluated periodically and can lag usage by several minutes.`,
	}
	c.AddCommand(&cobra.Command{
		Use: "get [id|name]", Short: "Show the project's monthly budget and this month's spend (default: the selected project)", Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			mc, err := a.Client(ctx)
			if err != nil {
				return err
			}
			id, err := budgetProjectID(cmd, mc, args)
			if err != nil {
				return err
			}
			b, err := mgmt.GetProjectBudget(ctx, mc, id)
			if err != nil {
				return err
			}
			return a.Printer().Object(b, projectBudgetKV(&b.V))
		},
	})
	set := &cobra.Command{
		Use: "set [id|name] --monthly-usd <amount>", Short: "Set or replace the project's monthly budget (default: the selected project)", Args: cobra.MaximumNArgs(1),
		Example: `  daytona project budget set --monthly-usd 500
  daytona project budget set ml-training --monthly-usd 1250.50`,
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			raw, _ := cmd.Flags().GetString("monthly-usd")
			if !cmd.Flags().Changed("monthly-usd") {
				return cerr.Usage("--monthly-usd is required")
			}
			cents, err := parseUSDCents(raw)
			if err != nil {
				return err
			}
			mc, err := a.Client(ctx)
			if err != nil {
				return err
			}
			id, err := budgetProjectID(cmd, mc, args)
			if err != nil {
				return err
			}
			b, err := mgmt.SetProjectBudget(ctx, mc, id, cents)
			if err != nil {
				return err
			}
			return a.Printer().Object(b, projectBudgetKV(&b.V))
		},
	}
	set.Flags().String("monthly-usd", "", "monthly budget in US dollars, at least 1.00 and at most two decimals (e.g. 500 or 499.99)")
	c.AddCommand(set)
	c.AddCommand(&cobra.Command{
		Use: "remove [id|name]", Short: "Remove the project's monthly budget (default: the selected project)", Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			label := "the selected project"
			if len(args) == 1 {
				label = "project " + args[0]
			}
			if err := a.PreflightConfirm("remove", "the monthly budget of "+label); err != nil {
				return err
			}
			mc, err := a.Client(ctx)
			if err != nil {
				return err
			}
			id, err := budgetProjectID(cmd, mc, args)
			if err != nil {
				return err
			}
			if err := a.Confirm("remove", "the monthly budget of project "+id); err != nil {
				return err
			}
			if err := mgmt.RemoveProjectBudget(ctx, mc, id); err != nil {
				return noBudgetHint(err)
			}
			return printAck(a, "project_id", id, "removed", fmt.Sprintf("Monthly budget removed from project %s.", id))
		},
	})
	return c
}

// budgetProjectID resolves the optional [id|name] argument to a project id,
// defaulting to the selected project as `project get` does.
func budgetProjectID(cmd *cobra.Command, mc *mgmtClient, args []string) (string, error) {
	a := FromContext(cmd.Context())
	var err error
	id := a.Profile.ProjectID()
	if len(args) == 1 {
		id, err = resolveProjectLookupID(cmd, mc, args[0])
		if err != nil {
			return "", err
		}
	}
	if id == "" {
		return "", cerr.MissingArg("a project", "an id/name argument or --project")
	}
	if len(args) == 0 && !looksLikeProjectUUID(id) {
		id, err = resolveProjectLookupID(cmd, mc, id)
		if err != nil {
			return "", err
		}
	}
	return id, nil
}

// noBudgetHint replaces the generic 404 hint on DELETE, where a 404 means
// the project has no budget. GET answers 200 with null fields instead.
func noBudgetHint(err error) error {
	var ce *cerr.Error
	var ae *api.APIError
	if errors.As(err, &ce) && errors.As(err, &ae) && ae.Status == http.StatusNotFound {
		ce.Hint = "the project has no monthly budget (set one with `daytona project budget set --monthly-usd <amount>`), or check the id and the selected --org/--project"
	}
	return err
}

// parseUSDCents parses a dollar amount ("500", "499.99", "$12.5") into whole
// cents without floating point. It refuses signs, exponents, separators,
// more than two decimals, and amounts under 1.00; the api enforces the
// upper bound.
func parseUSDCents(raw string) (int64, error) {
	s := strings.TrimPrefix(strings.TrimSpace(raw), "$")
	invalid := func() (int64, error) {
		return 0, cerr.Usage("--monthly-usd must be a dollar amount with at most two decimals, like 500 or 499.99 (got %q)", raw)
	}
	whole, frac, hasDot := strings.Cut(s, ".")
	if !allDigits(whole) || (hasDot && (!allDigits(frac) || len(frac) > 2)) {
		return invalid()
	}
	if len(strings.TrimLeft(whole, "0")) > 15 { // keeps the cents within int64
		return 0, cerr.Usage("--monthly-usd is too large (got %q)", raw)
	}
	dollars, err := strconv.ParseInt(whole, 10, 64)
	if err != nil {
		return invalid()
	}
	var fracCents int64
	if frac != "" {
		fracCents, _ = strconv.ParseInt((frac + "0")[:2], 10, 64)
	}
	cents := dollars*100 + fracCents
	if cents < 100 {
		return 0, cerr.Usage("--monthly-usd must be at least 1.00 (got %q)", raw)
	}
	return cents, nil
}

func allDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// formatUSDCents renders cents as "$1,234.56".
func formatUSDCents(cents int64) string {
	sign := ""
	if cents < 0 {
		sign, cents = "-", -cents
	}
	whole := strconv.FormatInt(cents/100, 10)
	var b strings.Builder
	for i, r := range whole {
		if i > 0 && (len(whole)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(r)
	}
	return fmt.Sprintf("%s$%s.%02d", sign, b.String(), cents%100)
}

func projectBudgetKV(b *gen.ProjectBudget) [][2]string {
	ts := func(t *time.Time) string {
		if t == nil {
			return "-"
		}
		return t.UTC().Format("2006-01-02 15:04 UTC")
	}
	spent := "not calculated yet"
	if b.SpentCents != nil {
		spent = formatUSDCents(*b.SpentCents)
	}
	monthly := "not set"
	if b.MonthlyLimitCents != nil {
		monthly = formatUSDCents(*b.MonthlyLimitCents)
	}
	periodStart := ""
	if b.PeriodStartDate != nil {
		periodStart = b.PeriodStartDate.Time.Format("2006-01-02") + " (UTC)"
	}
	// Status reports the hold, never "under budget": exceeded_at is not
	// a comparison of spent_cents against monthly_limit_cents.
	var status string
	switch {
	case b.ExceededAt != nil:
		status = "held (over budget) since " + ts(b.ExceededAt) + "; new sandboxes can't start"
	case b.SpentCents == nil:
		status = "not calculated yet"
	default:
		status = "not held"
	}
	// stop_at is null on a held budget when the grace period would end
	// after the month boundary: the hold lifts at 00:00 UTC on the 1st
	// first, so nothing is stopped.
	end := ts(b.StopAt)
	if b.ExceededAt != nil && b.StopAt == nil {
		end = "none this month (the hold lifts at 00:00 UTC on the 1st first)"
	}
	return [][2]string{
		{"Monthly budget", monthly},
		{"Period start", periodStart},
		{"Spent this month", spent},
		{"Spend updated", ts(b.EvaluatedAt)},
		{"Status", status},
		{"Running sandboxes end at", end},
	}
}
