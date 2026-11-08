// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"
	"net/http"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/spf13/cobra"
)

func newBillingAutomaticTopUpCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "automatic-top-up", Short: "Manage automatic top-up"}
	cmd.AddCommand(
		newBillingGetCmd[gen.AutomaticTopUp]("get", "/automatic-top-up", ""),
		newBillingAutomaticTopUpSetCmd(),
		newBillingDeleteCmd("delete", "/automatic-top-up", "Delete automatic top-up", "automatic top-up"),
	)
	return cmd
}

func newBillingAutomaticTopUpSetCmd() *cobra.Command {
	var thresholdCents int64
	var targetCents int64
	cmd := &cobra.Command{Use: "set", Short: "Set automatic top-up", Long: "Set automatic top-up using integer-cent thresholds. It charges the organization's payment method, so it asks for confirmation; in non-interactive mode (no terminal, -o json|yaml, or CI) pass --yes or set DAYTONA_YES=1. Requires write:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		if err := requireBillingFlag(cmd, "threshold-cents"); err != nil {
			return err
		}
		if err := requireBillingFlag(cmd, "target-cents"); err != nil {
			return err
		}
		const target = "automatic top-up, which charges the organization's payment method"
		if err := FromContext(cmd.Context()).PreflightConfirm("set", target); err != nil {
			return err
		}
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		if err := a.Confirm("set", fmt.Sprintf("automatic top-up of organization %s to %d cents when the balance falls below %d cents, charged to its payment method", orgID, targetCents, thresholdCents)); err != nil {
			return err
		}
		var payload api.Raw[gen.AutomaticTopUp]
		err = mc.Do(cmd.Context(), http.MethodPut, billingBasePath(orgID)+"/automatic-top-up", nil, gen.SetAutomaticTopUpRequest{ThresholdCents: thresholdCents, TargetCents: targetCents}, &payload)
		if err != nil {
			return err
		}
		return renderDynamicValue(a, payload, "")
	}}
	cmd.Flags().Int64Var(&thresholdCents, "threshold-cents", 0, "balance in cents that triggers a top-up")
	cmd.Flags().Int64Var(&targetCents, "target-cents", 0, "balance in cents targeted by a top-up")
	return cmd
}

func newBillingBalanceNotificationCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "balance-notification", Short: "Manage low-balance notifications"}
	cmd.AddCommand(
		newBillingGetCmd[gen.BalanceNotification]("get", "/balance-notification", ""),
		newBillingBalanceNotificationSetCmd(),
		newBillingDeleteCmd("delete", "/balance-notification", "Delete the low-balance notification", "the low-balance notification"),
	)
	return cmd
}

func newBillingBalanceNotificationSetCmd() *cobra.Command {
	var thresholdCents int64
	cmd := &cobra.Command{Use: "set", Short: "Set the low-balance notification", Long: "Set the low-balance notification using an integer-cent threshold. Requires write:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		if err := requireBillingFlag(cmd, "threshold-cents"); err != nil {
			return err
		}
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		var payload api.Raw[gen.BalanceNotification]
		err = mc.Do(cmd.Context(), http.MethodPut, billingBasePath(orgID)+"/balance-notification", nil, gen.SetBalanceNotificationRequest{ThresholdCents: thresholdCents}, &payload)
		if err != nil {
			return err
		}
		return renderDynamicValue(a, payload, "")
	}}
	cmd.Flags().Int64Var(&thresholdCents, "threshold-cents", 0, "notification threshold in integer cents")
	return cmd
}

func requireBillingFlag(cmd *cobra.Command, name string) error {
	if !cmd.Flags().Changed(name) {
		return cerr.Usage("--%s is required", name)
	}
	return nil
}

func newBillingDeleteCmd(use, suffix, short, target string) *cobra.Command {
	return &cobra.Command{Use: use, Short: short, Long: short + ". Asks for confirmation; in non-interactive mode (no terminal, -o json|yaml, or CI) pass --yes or set DAYTONA_YES=1. Requires write:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		a := FromContext(cmd.Context())
		if err := a.PreflightConfirm("delete", target); err != nil {
			return err
		}
		orgID, mc, _, err := billingClient(cmd)
		if err != nil {
			return err
		}
		if err := a.Confirm("delete", target+" of organization "+orgID); err != nil {
			return err
		}
		if err := mc.Do(cmd.Context(), http.MethodDelete, billingBasePath(orgID)+suffix, nil, nil, nil); err != nil {
			return err
		}
		return printAck(a, "org_id", orgID, "deleted", "")
	}}
}

func newBillingCouponCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "coupon", Short: "Coupon subcommands"}
	cmd.AddCommand(&cobra.Command{Use: "redeem <code>", Short: "Redeem a billing coupon", Long: "Redeem a billing coupon. Requires write:billing.", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		var payload api.Raw[gen.CouponRedemption]
		err = mc.Do(cmd.Context(), http.MethodPost, billingBasePath(orgID)+"/coupons/redeem", nil, gen.RedeemCouponRequest{Code: args[0]}, &payload)
		if err != nil {
			return err
		}
		return renderDynamicValue(a, payload, "")
	}})
	return cmd
}

func newBillingPlanCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "plan", Short: "Billing plan subcommands"}
	cmd.AddCommand(
		newBillingGetCmd[gen.BillingPlan]("get", "/plan", ""),
		newBillingGetCmd[gen.PlanList]("list", "/plans", "plans"),
		&cobra.Command{Use: "set <starter|team|enterprise>", Short: "Set the billing plan", Long: "Set the organization's billing plan. It changes what the organization is billed, so it asks for confirmation; in non-interactive mode (no terminal, -o json|yaml, or CI) pass --yes or set DAYTONA_YES=1. Requires write:billing.", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
			plan := strings.ToLower(args[0])
			if plan != "starter" && plan != "team" && plan != "enterprise" {
				return cerr.Usage("plan must be starter, team, or enterprise")
			}
			if err := FromContext(cmd.Context()).PreflightConfirm("set", "the billing plan to "+plan); err != nil {
				return err
			}
			orgID, mc, a, err := billingClient(cmd)
			if err != nil {
				return err
			}
			if err := a.Confirm("set", "the billing plan of organization "+orgID+" to "+plan); err != nil {
				return err
			}
			var payload api.Raw[gen.BillingPlan]
			err = mc.Do(cmd.Context(), http.MethodPut, billingBasePath(orgID)+"/plan", nil, gen.SetPlanRequest{Plan: gen.PlanName(plan)}, &payload)
			if err != nil {
				return err
			}
			return renderDynamicValue(a, payload, "")
		}},
	)
	return cmd
}

func newBillingContactCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "contact", Short: "Billing contact subcommands"}
	cmd.AddCommand(newBillingGetCmd[gen.BillingContact]("get", "/contact", ""), newBillingContactSetCmd())
	return cmd
}

func newBillingContactSetCmd() *cobra.Command {
	var email string
	var clear bool
	cmd := &cobra.Command{Use: "set", Short: "Set or clear the billing contact", Long: "Set or clear the billing contact email. Requires write:billing.", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		if clear == cmd.Flags().Changed("email") {
			return cerr.Usage("pass exactly one of --email or --clear")
		}
		if strings.TrimSpace(email) == "" && !clear {
			return cerr.Usage("--email must not be blank; use --clear to clear the billing contact")
		}
		var value *string
		if !clear {
			value = &email
		}
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		var payload api.Raw[gen.BillingContact]
		err = mc.Do(cmd.Context(), http.MethodPut, billingBasePath(orgID)+"/contact", nil, billingContactRequest{Email: value}, &payload)
		if err != nil {
			return err
		}
		return renderDynamicValue(a, payload, "")
	}}
	cmd.Flags().StringVar(&email, "email", "", "billing contact email")
	cmd.Flags().BoolVar(&clear, "clear", false, "restore the default billing contact")
	return cmd
}

type billingContactRequest struct {
	Email *string `json:"email"`
}
