// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/spf13/cobra"
)

func init() { Register(newBillingCmd) }

func newBillingCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "billing", Short: "Organization billing: usage, invoices, plans, and payment settings", GroupID: GroupManagement}
	cmd.AddCommand(
		newBillingGetCmd[gen.Wallet]("wallet", "/wallet", ""),
		newBillingBalancesCmd(),
		newBillingUsageCmd(),
		newBillingUsagePeriodsCmd(),
		newBillingPaymentMethodsCmd(),
		newBillingChargesCmd(),
		newBillingInvoicesCmd(),
		newBillingInvoiceCmd(),
		newBillingTopUpCmd(),
		newBillingSessionCmd("portal-session", "/portal-sessions", "Create a billing portal session"),
		newBillingSessionCmd("payment-method-session", "/payment-method-sessions", "Create a payment-method setup session"),
		newBillingAutomaticTopUpCmd(),
		newBillingBalanceNotificationCmd(),
		newBillingCouponCmd(),
		newBillingPlanCmd(),
		newBillingContactCmd(),
		newBillingGetCmd[gen.BillingProfile]("profile", "/profile", ""),
	)
	return cmd
}

func billingClient(cmd *cobra.Command) (string, *mgmtClient, *app.App, error) {
	a := FromContext(cmd.Context())
	ctx := cmd.Context()
	mc, err := a.Client(ctx)
	if err != nil {
		return "", nil, nil, err
	}
	orgID, err := a.RequireOrg(ctx, pickOrg(a))
	if err != nil {
		return "", nil, nil, err
	}
	return orgID, mc, a, nil
}

func newBillingGetCmd[T any](use string, suffix string, listKey string) *cobra.Command {
	return &cobra.Command{Use: use, Short: "Read billing " + use, Long: "Read billing " + use + ". Requires read:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		payload, err := billingGet[api.Raw[T]](cmd.Context(), mc, billingBasePath(orgID)+suffix, nil)
		if err != nil {
			return err
		}
		return renderDynamicValue(a, payload, listKey)
	}}
}

func addBillingPageFlags(cmd *cobra.Command, flags *pageFlags, defaultLimit, maxLimit int64) {
	cmd.Flags().Int64Var(&flags.limit, "limit", defaultLimit, fmt.Sprintf("items per page, 1 to %d", maxLimit))
	cmd.Flags().StringVar(&flags.cursor, "cursor", "", "opaque continuation token")
	cmd.Flags().BoolVar(&flags.all, "all", false, "follow every page until exhausted")
	cmd.PreRunE = func(cmd *cobra.Command, _ []string) error {
		if cmd.Flags().Changed("limit") && (flags.limit < 1 || flags.limit > maxLimit) {
			return cerr.Usage("--limit must be between 1 and %d", maxLimit)
		}
		return nil
	}
}

func runBillingPages[T any](ctx context.Context, flags pageFlags, fetch func(context.Context, string) (output.List[T], error)) (output.List[T], error) {
	if flags.all {
		return collectPaged(ctx, flags.cursor, fetch)
	}
	return fetch(ctx, flags.cursor)
}

func newBillingBalancesCmd() *cobra.Command {
	flags := pageFlags{}
	cmd := &cobra.Command{Use: "balances", Short: "List billing balances", Long: "List itemized billing balances. Requires read:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		page, err := runBillingPages(cmd.Context(), flags, func(ctx context.Context, cursor string) (output.List[api.Raw[gen.Balance]], error) {
			return getRawList[gen.Balance](ctx, mc, billingBasePath(orgID)+"/balances", listQuery(flags, cursor), "balances")
		})
		return renderBillingList(a, page, err)
	}}
	addBillingPageFlags(cmd, &flags, 25, 100)
	return cmd
}

func newBillingPaymentMethodsCmd() *cobra.Command {
	flags := pageFlags{}
	cmd := &cobra.Command{Use: "payment-methods", Short: "List payment methods", Long: "List saved payment methods. Requires read:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		page, err := runBillingPages(cmd.Context(), flags, func(ctx context.Context, cursor string) (output.List[api.Raw[gen.PaymentMethod]], error) {
			return getRawList[gen.PaymentMethod](ctx, mc, billingBasePath(orgID)+"/payment-methods", listQuery(flags, cursor), "payment_methods")
		})
		return renderBillingList(a, page, err)
	}}
	addBillingPageFlags(cmd, &flags, 20, 100)
	return cmd
}

func newBillingChargesCmd() *cobra.Command {
	flags := pageFlags{}
	cmd := &cobra.Command{Use: "charges", Short: "List billing charges", Long: "List charge history. Requires read:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		page, err := runBillingPages(cmd.Context(), flags, func(ctx context.Context, cursor string) (output.List[api.Raw[gen.Charge]], error) {
			return getRawList[gen.Charge](ctx, mc, billingBasePath(orgID)+"/charges", listQuery(flags, cursor), "charges")
		})
		return renderBillingList(a, page, err)
	}}
	addBillingPageFlags(cmd, &flags, 25, 100)
	return cmd
}

func newBillingInvoicesCmd() *cobra.Command {
	flags := pageFlags{}
	var unpaid bool
	cmd := &cobra.Command{Use: "invoices", Short: "List billing invoices", Long: "List invoice history. Requires read:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		page, err := runBillingPages(cmd.Context(), flags, func(ctx context.Context, cursor string) (output.List[api.Raw[gen.Invoice]], error) {
			query := listQuery(flags, cursor)
			if unpaid {
				query.Set("unpaid", "true")
			}
			return getRawList[gen.Invoice](ctx, mc, billingBasePath(orgID)+"/invoices", query, "invoices")
		})
		return renderBillingList(a, page, err)
	}}
	addBillingPageFlags(cmd, &flags, 25, 100)
	cmd.Flags().BoolVar(&unpaid, "unpaid", false, "return only unpaid invoices")
	return cmd
}

func newBillingUsageCmd() *cobra.Command {
	return &cobra.Command{Use: "usage", Short: "Read current priced usage", Long: "Read the open billing usage period. Requires read:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		payload, err := getRawList[billingUsagePeriod](cmd.Context(), mc, billingBasePath(orgID)+"/usage-periods", url.Values{"limit": {"1"}}, "usage_periods")
		if err != nil {
			return err
		}
		for _, period := range payload.Items {
			if strings.EqualFold(period.V.Status, "open") {
				return renderDynamicValue(a, period, "")
			}
		}
		return cerr.New(cerr.ExitNotFound, "no open billing usage period")
	}}
}

func newBillingUsagePeriodsCmd() *cobra.Command {
	flags := pageFlags{}
	cmd := &cobra.Command{Use: "usage-periods", Short: "List priced usage periods", Long: "List current and past priced usage periods. Requires read:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		page, err := runBillingPages(cmd.Context(), flags, func(ctx context.Context, cursor string) (output.List[api.Raw[billingUsagePeriod]], error) {
			return getRawList[billingUsagePeriod](ctx, mc, billingBasePath(orgID)+"/usage-periods", listQuery(flags, cursor), "usage_periods")
		})
		return renderBillingList(a, page, err)
	}}
	addBillingPageFlags(cmd, &flags, 12, 60)
	return cmd
}

const (
	topUpMinCents = 1_000
	topUpMaxCents = 99_999_999
)

func newBillingTopUpCmd() *cobra.Command {
	var amount int64
	cmd := &cobra.Command{Use: "top-up [amount-cents]", Short: "Create a manual top-up session", Long: "Create a manual top-up checkout session. Amounts are integer cents. Requires write:billing.", Args: cobra.MaximumNArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		if len(args) == 1 {
			if cmd.Flags().Changed("amount-cents") {
				return cerr.Usage("pass the amount either positionally or with --amount-cents")
			}
			parsed, err := strconv.ParseInt(args[0], 10, 64)
			if err != nil {
				return cerr.Usage("amount must be a positive integer number of cents")
			}
			amount = parsed
		}
		if amount < topUpMinCents || amount > topUpMaxCents {
			return cerr.Usage("amount must be between %d and %d cents ($10.00–$999,999.99)", topUpMinCents, topUpMaxCents)
		}
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		var payload api.Raw[gen.BillingSession]
		err = mc.Do(cmd.Context(), http.MethodPost, billingBasePath(orgID)+"/top-up-sessions", nil, gen.CreateTopUpSessionRequest{AmountCents: amount}, &payload)
		if err != nil {
			return err
		}
		return renderDynamicValue(a, payload, "")
	}}
	cmd.Flags().Int64Var(&amount, "amount-cents", 0, "top-up amount in integer cents")
	return cmd
}

func newBillingSessionCmd(use string, suffix string, short string) *cobra.Command {
	var open bool
	cmd := &cobra.Command{Use: use, Short: short, Long: short + ". Requires write:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		var payload api.Raw[gen.BillingSession]
		if err := mc.Do(cmd.Context(), http.MethodPost, billingBasePath(orgID)+suffix, nil, nil, &payload); err != nil {
			return err
		}
		if open {
			if err := openHTTPSURL(payload.V.Url); err != nil {
				return err
			}
		}
		return renderDynamicValue(a, payload, "")
	}}
	cmd.Flags().BoolVar(&open, "open", false, "open the minted URL in your browser")
	return cmd
}
