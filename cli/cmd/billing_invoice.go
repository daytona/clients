// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"io"
	"net/http"
	"net/url"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
	"github.com/spf13/cobra"
)

func newBillingInvoiceCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "invoice", Short: "Invoice subcommands"}
	cmd.AddCommand(newBillingInvoicePDFCmd(), newBillingInvoicePaymentSessionCmd())
	return cmd
}

func newBillingInvoicePDFCmd() *cobra.Command {
	var outFile string
	cmd := &cobra.Command{Use: "pdf <id>", Short: "Download one invoice PDF", Long: "Download one invoice PDF. Requires read:billing.", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		resp, err := mc.Stream(cmd.Context(), http.MethodGet, billingBasePath(orgID)+"/invoices/"+url.PathEscape(args[0])+"/pdf", nil, nil, "")
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		if outFile == "" || outFile == "-" {
			_, err = io.Copy(a.Stdout, resp.Body)
			return err
		}
		return toolboxclient.WriteDownloadedFile(resp.Body, outFile)
	}}
	cmd.Flags().StringVar(&outFile, "out", "", "write the PDF to a file instead of stdout")
	return cmd
}

func newBillingInvoicePaymentSessionCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "payment-session <id>", Short: "Create a payment session for one invoice", Long: "Create a hosted payment session for one invoice. Requires write:billing.", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		orgID, mc, a, err := billingClient(cmd)
		if err != nil {
			return err
		}
		var payload api.Raw[gen.BillingSession]
		if err := mc.Do(cmd.Context(), http.MethodPost, billingBasePath(orgID)+"/invoices/"+url.PathEscape(args[0])+"/payment-sessions", nil, nil, &payload); err != nil {
			return err
		}
		return renderDynamicValue(a, payload, "")
	}}
	return cmd
}
