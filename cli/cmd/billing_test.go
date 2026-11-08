// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
	"github.com/stretchr/testify/require"
)

func TestBillingPaymentMethods_rendersTopLevelArrayInTableMode(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/payment-methods", http.StatusOK, map[string]any{"payment_methods": []map[string]any{paymentMethodFixture()}, "next_cursor": nil})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "payment-methods")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "pm_123")
	require.Contains(t, stdout, "visa")
}

func TestBillingPaymentMethods_wrapsTopLevelArrayInJSON(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/payment-methods", http.StatusOK, map[string]any{"payment_methods": []map[string]any{paymentMethodFixture()}, "next_cursor": nil})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "-o", "json", "billing", "payment-methods")
	require.Equal(t, 0, code, stderr)
	payload := mustJSON(t, stdout)
	require.Equal(t, false, payload["incomplete"])
	require.Len(t, payload["items"].([]any), 1)
}

func TestBillingUnpaidInvoices_rendersTopLevelArrayInTableMode(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/invoices", http.StatusOK, map[string]any{"invoices": []map[string]any{invoiceFixture("inv_123")}, "next_cursor": nil})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "invoices", "--unpaid")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "inv_123")
	require.Equal(t, "limit=25&unpaid=true", api.requests[0].Query)
}

func TestBillingUsagePast_rendersTopLevelArrayInTableMode(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/usage-periods", http.StatusOK, usagePeriodPageFixture("closed", nil))
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "usage-periods")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "1000")
	require.Contains(t, stdout, "2026-09-30")
}

func TestBillingBalances_rendersDataWrapperAsListInTableMode(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/balances", http.StatusOK, map[string]any{
		"balances":    []map[string]any{{"id": "bal_123", "name": "credits", "type": "credit", "balance_cents": 1000, "granted_amount_cents": 1000, "currency": "usd", "created_at": "2026-09-01T00:00:00Z"}},
		"next_cursor": nil,
	})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "balances")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "bal_123")
}

func TestBillingBalances_wrapsDataWrapperInYAML(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/balances", http.StatusOK, map[string]any{
		"balances":    []map[string]any{{"id": "bal_123", "name": "credits", "type": "credit", "balance_cents": 1000, "granted_amount_cents": 1000, "currency": "usd", "created_at": "2026-09-01T00:00:00Z"}},
		"next_cursor": nil,
	})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "-o", "yaml", "billing", "balances")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "items:")
	require.Contains(t, stdout, "incomplete: false")
}

func TestBillingBalances_wrapsPaginationMetadataInJSON(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/balances", http.StatusOK, map[string]any{
		"balances":    []map[string]any{{"id": "bal_123", "name": "credits", "type": "credit", "balance_cents": 1000, "granted_amount_cents": 1000, "currency": "usd", "created_at": "2026-09-01T00:00:00Z"}},
		"next_cursor": "page_2",
	})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "-o", "json", "billing", "balances", "--limit", "5", "--cursor", "page_1")
	require.Equal(t, 0, code, stderr)
	payload := mustJSON(t, stdout)
	require.Equal(t, false, payload["incomplete"])
	require.Equal(t, "page_2", payload["next_cursor"])
	require.Equal(t, "cursor=page_1&limit=5", api.requests[0].Query)
}

func TestBillingInvoices_rendersItemsWrapperAsListInTableMode(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/invoices", http.StatusOK, map[string]any{
		"invoices":    []map[string]any{invoiceFixture("inv_123")},
		"next_cursor": nil,
	})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "invoices")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "inv_123")
}

func TestBillingCharges_forwardsCursorAndSurfacesContinuationHint(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/charges", http.StatusOK, map[string]any{
		"charges":     []map[string]any{{"id": "ch_123", "amount_cents": 1000, "currency": "usd", "description": "charge", "has_pdf": false, "has_invoice": false, "invoice_url": nil, "receipt_url": nil, "failure_message": nil, "created_at": "2026-09-01T00:00:00Z", "status": "succeeded"}},
		"next_cursor": "ch_456",
	})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "charges", "--limit", "5", "--cursor", "ch_0", "-o", "table")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "cursor=ch_0&limit=5", api.requests[0].Query)
	require.Contains(t, stdout, "--cursor ch_456")
}

func TestBillingInvoices_surfaceTruncatedAndPageHint(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/invoices", http.StatusOK, map[string]any{
		"invoices":    []map[string]any{invoiceFixture("inv_123")},
		"next_cursor": "invoice_2",
	})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "invoices", "--limit", "1", "--cursor", "invoice_1", "-o", "table")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "cursor=invoice_1&limit=1", api.requests[0].Query)
	require.Contains(t, stdout, "--cursor invoice_2")
}

func TestBillingInvoices_lastPageDoesNotHintNextPageWhenTruncated(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/invoices", http.StatusOK, map[string]any{
		"invoices":    []map[string]any{invoiceFixture("inv_123")},
		"next_cursor": nil,
	})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "invoices", "-o", "table")
	require.Equal(t, 0, code, stderr)
	require.NotContains(t, stdout, "--cursor")
}

func TestBillingTopUp_acceptsPositionalAmount(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodPost, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/top-up-sessions", http.StatusCreated, map[string]any{"url": "https://checkout.example", "expires_at": "2026-10-20T00:00:00Z"})
	_, stderr, code := runMgmt(t, api, cfgDir(t), "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "top-up", "2500")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, map[string]any{"amount_cents": float64(2500)}, api.requests[0].Body)
}

func TestBillingInvoicePDF_writesToInjectedStdout(t *testing.T) {
	api := newFakeAPI(t)
	api.handle(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/invoices/inv-1/pdf", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/pdf")
		_, _ = w.Write([]byte("PDF"))
	})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "invoice", "pdf", "inv-1")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "PDF", stdout)
}

func TestBillingInvoiceCommands_escapeInvoiceIDInURLs(t *testing.T) {
	api := newFakeAPI(t)
	api.handle(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/invoices/inv/part/pdf", func(w http.ResponseWriter, r *http.Request) {
		if got := r.URL.EscapedPath(); got != "/orgs/0000000a-0000-4000-8000-00000000a001/billing/invoices/inv%2Fpart/pdf" {
			t.Errorf("EscapedPath = %q", got)
		}
		w.Header().Set("Content-Type", "application/pdf")
		_, _ = w.Write([]byte("PDF"))
	})
	api.handle(http.MethodPost, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/invoices/inv/part/payment-sessions", func(w http.ResponseWriter, r *http.Request) {
		if got := r.URL.EscapedPath(); got != "/orgs/0000000a-0000-4000-8000-00000000a001/billing/invoices/inv%2Fpart/payment-sessions" {
			t.Errorf("EscapedPath = %q", got)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"url":"https://billing.example/pay","expires_at":null}`))
	})

	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "invoice", "pdf", "inv/part")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "PDF", stdout)

	stdout, stderr, code = run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "-o", "json", "billing", "invoice", "payment-session", "inv/part")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "https://billing.example/pay", mustJSON(t, stdout)["url"])
}

func TestBillingCommands_escapeOrgIDPathSegment(t *testing.T) {
	var escaped []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		escaped = append(escaped, r.URL.EscapedPath())
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/orgs/org?1/billing/wallet":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"balance_cents":1000,"free_credits_balance_cents":0,"paid_credits_balance_cents":1000,"currency":"usd","billing_type":"pre-paid","total_amount_due_this_month_cents":0,"total_spent_this_month_cents":0,"automatic_top_up":{"enabled":false,"threshold_cents":null,"target_cents":null,"currency":"usd"}}`))
		case r.Method == http.MethodPost && r.URL.Path == "/orgs/org?1/billing/top-up-sessions":
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"url":"https://checkout.example","expires_at":null}`))
		case r.Method == http.MethodGet && r.URL.Path == "/orgs/org?1/billing/invoices/inv-1/pdf":
			w.Header().Set("Content-Type", "application/pdf")
			_, _ = w.Write([]byte("PDF"))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()

	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "--org", "org?1", "billing", "wallet")
	require.Equal(t, 0, code, stderr)
	_, stderr, code = run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "--org", "org?1", "billing", "top-up", "2500")
	require.Equal(t, 0, code, stderr)
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "--org", "org?1", "billing", "invoice", "pdf", "inv-1")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, "PDF", stdout)
	// A non-UUID --org is first offered to the name resolver (GET /orgs, which
	// this fake answers 404 → the value is passed through); only the billing
	// paths are under test here.
	var billing []string
	for _, p := range escaped {
		if strings.Contains(p, "/billing/") {
			billing = append(billing, p)
		}
	}
	require.Equal(t, []string{"/orgs/org%3F1/billing/wallet", "/orgs/org%3F1/billing/top-up-sessions", "/orgs/org%3F1/billing/invoices/inv-1/pdf"}, billing)
}

func TestBillingHelp_mentionsRequiredPermissions(t *testing.T) {
	tests := []struct {
		args       []string
		permission string
	}{
		{[]string{"billing", "wallet", "--help"}, "read:billing"},
		{[]string{"billing", "balances", "--help"}, "read:billing"},
		{[]string{"billing", "usage", "--help"}, "read:billing"},
		{[]string{"billing", "usage-periods", "--help"}, "read:billing"},
		{[]string{"billing", "charges", "--help"}, "read:billing"},
		{[]string{"billing", "invoices", "--help"}, "read:billing"},
		{[]string{"billing", "payment-methods", "--help"}, "read:billing"},
		{[]string{"billing", "profile", "--help"}, "read:billing"},
		{[]string{"billing", "portal-session", "--help"}, "write:billing"},
		{[]string{"billing", "payment-method-session", "--help"}, "write:billing"},
		{[]string{"billing", "top-up", "--help"}, "write:billing"},
		{[]string{"billing", "invoice", "pdf", "--help"}, "read:billing"},
		{[]string{"billing", "invoice", "payment-session", "--help"}, "write:billing"},
	}
	for _, tc := range tests {
		stdout, stderr, code := run(t, cfgDir(t), nil, tc.args...)
		require.Equal(t, 0, code, stderr)
		require.Contains(t, stdout, tc.permission, strings.Join(tc.args, " "))
	}
}

type failingReader struct {
	chunks [][]byte
	err    error
	index  int
}

func (r *failingReader) Read(p []byte) (int, error) {
	if r.index >= len(r.chunks) {
		return 0, r.err
	}
	n := copy(p, r.chunks[r.index])
	r.index++
	if r.index >= len(r.chunks) {
		return n, r.err
	}
	return n, nil
}

func TestSharedDownloadedFileWriter_midStreamFailureLeavesOriginalUntouched(t *testing.T) {
	path := filepath.Join(t.TempDir(), "invoice.pdf")
	require.NoError(t, os.WriteFile(path, []byte("ORIGINAL"), 0o600))
	err := toolboxclient.WriteDownloadedFile(&failingReader{chunks: [][]byte{[]byte("NEW")}, err: errors.New("boom")}, path)
	require.Error(t, err)
	body, readErr := os.ReadFile(path)
	require.NoError(t, readErr)
	require.Equal(t, "ORIGINAL", string(body))
}

func TestRenderDynamicRows_usesUnionOfColumnsInFirstSeenOrder(t *testing.T) {
	var buf bytes.Buffer
	err := renderDynamicRows(output.Printer{W: &buf, Format: output.FormatTable}, []map[string]any{
		{"id": "one", "name": "first"},
		{"id": "two", "extra": "value"},
	})
	require.NoError(t, err)
	require.Contains(t, buf.String(), "id")
	require.Contains(t, buf.String(), "name")
	require.Contains(t, buf.String(), "extra")
	require.Contains(t, buf.String(), "two")
	require.Contains(t, buf.String(), "value")
}

func TestOpenHTTPSURLRefusesNonHTTPS(t *testing.T) {
	for _, bad := range []string{"file:///etc/passwd", "javascript:alert(1)", "http://billing.example/portal", "not a url", "https://"} {
		if err := openHTTPSURL(bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
}

func TestBillingPortalOpenRequiresURL(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/orgs/0000000a-0000-4000-8000-00000000a001/billing/portal-sessions" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"url":"","expires_at":null}`))
	}))
	defer server.Close()
	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "portal-session", "--open")
	require.Equal(t, cerr.ExitFailure, code, stderr)
	require.Contains(t, stderr, "not an https URL")
}

func TestBillingTopUp_rejectsOutOfBoundsBeforeRequest(t *testing.T) {
	api := newFakeAPI(t)
	for _, amt := range []string{"999", "100000000", "0"} {
		_, stderr, code := runMgmt(t, api, cfgDir(t), "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "top-up", amt)
		require.Equal(t, cerr.ExitUsage, code, "amount %s: %s", amt, stderr)
	}
	require.Empty(t, api.requests, "no request may be made for an out-of-bounds amount")
}

func TestBillingUsage_readsOpenPeriodFromUsagePeriods(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/0000000a-0000-4000-8000-00000000a001/billing/usage-periods", http.StatusOK, usagePeriodPageFixture("open", nil))
	stdout, stderr, code := runMgmt(t, api, cfgDir(t), "--org", "0000000a-0000-4000-8000-00000000a001", "billing", "usage")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "open")
	require.Equal(t, "limit=1", api.requests[0].Query)
}

func TestBillingSessions_keepNullExpiresAtInJSON(t *testing.T) {
	orgID := "0000000a-0000-4000-8000-00000000a001"
	tests := []struct {
		name string
		path string
		args []string
	}{
		{name: "top-up", path: "/top-up-sessions", args: []string{"billing", "top-up", "2500"}},
		{name: "portal", path: "/portal-sessions", args: []string{"billing", "portal-session"}},
		{name: "payment method", path: "/payment-method-sessions", args: []string{"billing", "payment-method-session"}},
		{name: "invoice payment", path: "/invoices/inv-1/payment-sessions", args: []string{"billing", "invoice", "payment-session", "inv-1"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			api := newFakeAPI(t)
			api.on(http.MethodPost, "/orgs/"+orgID+"/billing"+tt.path, http.StatusCreated, map[string]any{"url": "https://billing.example/session", "expires_at": nil})
			stdout, stderr, code := runMgmt(t, api, cfgDir(t), append([]string{"--org", orgID}, tt.args...)...)
			require.Equal(t, 0, code, stderr)
			payload := mustJSON(t, stdout)
			expiry, ok := payload["expires_at"]
			require.True(t, ok, "an unbounded session must still report expires_at")
			require.Nil(t, expiry)
			require.Equal(t, "https://billing.example/session", payload["url"])
		})
	}
}

func TestBillingSession_rendersExpiryInYAML(t *testing.T) {
	orgID := "0000000a-0000-4000-8000-00000000a001"
	api := newFakeAPI(t)
	api.on(http.MethodPost, "/orgs/"+orgID+"/billing/portal-sessions", http.StatusCreated, map[string]any{"url": "https://billing.example/portal", "expires_at": nil})
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "--org", orgID, "-o", "yaml", "billing", "portal-session")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "expires_at: null")

	bounded := newFakeAPI(t)
	bounded.on(http.MethodPost, "/orgs/"+orgID+"/billing/portal-sessions", http.StatusCreated, map[string]any{"url": "https://billing.example/portal", "expires_at": "2026-10-20T00:00:00Z"})
	stdout, stderr, code = run(t, cfgDir(t), nil, "--api-url", bounded.srv.URL, "--api-key", "k", "--org", orgID, "-o", "yaml", "billing", "portal-session")
	require.Equal(t, 0, code, stderr)
	require.Contains(t, stdout, "2026-10-20T00:00:00Z")
}

func usagePeriodPageFixture(status string, nextCursor any) map[string]any {
	return map[string]any{
		"usage_periods": []map[string]any{{
			"start_at": "2026-09-01T00:00:00Z", "end_at": "2026-09-30T00:00:00Z", "issued_at": "2026-09-30T00:00:00Z",
			"status": status, "amount_cents": 1000, "total_amount_cents": 1200, "taxes_amount_cents": 200, "currency": "usd", "charges": []any{},
		}},
		"next_cursor": nextCursor,
	}
}
