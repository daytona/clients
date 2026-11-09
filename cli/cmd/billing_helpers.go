// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/auth"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
)

func billingGet[T any](ctx context.Context, client *api.Client, path string, query url.Values) (T, error) {
	var out T
	err := client.Do(ctx, http.MethodGet, path, query, nil, &out)
	return out, err
}

func billingBasePath(orgID string) string {
	return "/orgs/" + url.PathEscape(orgID) + "/billing"
}

func openHTTPSURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Host == "" {
		return cerr.New(cerr.ExitFailure, "refusing to open %q: not an https URL", raw)
	}
	return auth.OpenBrowser(u.String())
}

func renderBillingList[T any](a *app.App, page output.List[T], err error) error {
	if err != nil {
		return err
	}
	return output.ListOut(a.Printer(), page, func(items []T) output.Table {
		rows, rowsErr := dynamicRows(items)
		if rowsErr != nil {
			return output.Table{}
		}
		return dynamicRowsTable(rows)
	})
}

type billingUsageCharge struct {
	BillableMetric string  `json:"billable_metric"`
	EventsCount    int64   `json:"events_count"`
	Name           string  `json:"name"`
	Quantity       float64 `json:"quantity"`
	TotalCents     int64   `json:"total_cents"`
	Units          string  `json:"units"`
}

type billingUsagePeriod struct {
	AmountCents      int64                `json:"amount_cents"`
	Charges          []billingUsageCharge `json:"charges"`
	Currency         string               `json:"currency"`
	EndAt            time.Time            `json:"end_at"`
	IssuedAt         time.Time            `json:"issued_at"`
	StartAt          time.Time            `json:"start_at"`
	Status           string               `json:"status"`
	TaxesAmountCents int64                `json:"taxes_amount_cents"`
	TotalAmountCents int64                `json:"total_amount_cents"`
}

func renderDynamicValue(a *app.App, payload any, listKey string) error {
	generic, err := toDynamicValue(payload)
	if err != nil {
		return err
	}
	if items, ok := rowsFromDynamic(generic); ok {
		return renderDynamicRows(a.Printer(), items)
	}
	if listKey != "" {
		// A list command always prints the list envelope: a missing or null
		// list is an empty one.
		node, _ := generic.(map[string]any)
		if items, ok := rowsFromDynamic(node[listKey]); ok {
			return renderDynamicRows(a.Printer(), items)
		}
		if node[listKey] == nil {
			return renderDynamicRows(a.Printer(), []map[string]any{})
		}
		return cerr.New(cerr.ExitFailure, "the %s in the response is not a list of objects", listKey)
	}
	if node, ok := generic.(map[string]any); ok {
		return a.Printer().Object(node, sortedKVFromMap(node))
	}
	return a.Printer().Object(payload, nil)
}

func toDynamicValue(payload any) (any, error) {
	raw, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var generic any
	if err := dec.Decode(&generic); err != nil {
		return nil, err
	}
	return generic, nil
}

func rowsFromDynamic(value any) ([]map[string]any, bool) {
	raw, ok := value.([]any)
	if !ok {
		return nil, false
	}
	items := make([]map[string]any, 0, len(raw))
	for _, entry := range raw {
		row, ok := entry.(map[string]any)
		if !ok {
			return nil, false
		}
		items = append(items, row)
	}
	return items, true
}

func dynamicRows(value any) ([]map[string]any, error) {
	generic, err := toDynamicValue(value)
	if err != nil {
		return nil, err
	}
	items, ok := rowsFromDynamic(generic)
	if !ok {
		return nil, cerr.New(cerr.ExitFailure, "billing payload is not an item list")
	}
	return items, nil
}

func renderDynamicRows(p output.Printer, items []map[string]any) error {
	return output.ListOut(p, output.List[map[string]any]{Items: items}, func(rows []map[string]any) output.Table {
		return dynamicRowsTable(rows)
	})
}

func dynamicRowsTable(items []map[string]any) output.Table {
	if len(items) == 0 {
		return output.Table{}
	}
	keys := make([]string, 0)
	seen := make(map[string]struct{})
	for _, item := range items {
		for _, k := range orderedDynamicKeys(item) {
			if _, ok := seen[k]; ok {
				continue
			}
			seen[k] = struct{}{}
			keys = append(keys, k)
		}
	}
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		row := make([]string, 0, len(keys))
		for _, key := range keys {
			row = append(row, jsonCell(item[key]))
		}
		rows = append(rows, row)
	}
	return output.Table{Headers: keys, Rows: rows}
}

func orderedDynamicKeys(item map[string]any) []string {
	raw, err := json.Marshal(item)
	if err != nil {
		keys := make([]string, 0, len(item))
		for k := range item {
			keys = append(keys, k)
		}
		return keys
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	tok, err := dec.Token()
	if err != nil {
		return nil
	}
	if _, ok := tok.(json.Delim); !ok {
		return nil
	}
	keys := make([]string, 0, len(item))
	for dec.More() {
		keyToken, keyErr := dec.Token()
		if keyErr != nil {
			break
		}
		if key, ok := keyToken.(string); ok {
			keys = append(keys, key)
		}
		var value any
		if err := dec.Decode(&value); err != nil {
			break
		}
	}
	return keys
}
