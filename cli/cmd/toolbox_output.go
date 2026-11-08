// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"strings"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/output"
)

// Toolbox-derived JSON and YAML output is the toolbox API's JSON with every
// key converted to snake_case (the toolbox speaks camelCase) and signal
// names bare (`SIGTERM` → `TERM`), as everywhere else in the CLI. Tables are
// unchanged.

func printToolboxObject(a *app.App, v any, kv [][2]string) error {
	if a.Format == output.FormatTable {
		return a.Printer().Object(v, kv)
	}
	converted, err := toolboxJSON(v)
	if err != nil {
		return err
	}
	return a.Printer().Object(converted, kv)
}

func printToolboxList[T any](a *app.App, l output.List[api.Raw[T]], tbl func([]T) output.Table) error {
	if a.Format == output.FormatTable {
		return listOutRaw(a.Printer(), l, tbl)
	}
	if l.Items == nil {
		l.Items = []api.Raw[T]{}
	}
	converted, err := toolboxJSON(l)
	if err != nil {
		return err
	}
	return a.Printer().Object(converted, nil)
}

// toolboxJSON converts v's JSON to the CLI's toolbox output form.
func toolboxJSON(v any) (any, error) {
	converted, err := output.SnakeCase(v)
	if err != nil {
		return nil, err
	}
	return bareSignals(converted), nil
}

// bareSignals strips the `SIG` prefix from every "signal" value.
func bareSignals(v any) any {
	switch node := v.(type) {
	case map[string]any:
		for k, child := range node {
			if s, ok := child.(string); k == "signal" && ok {
				node[k] = strings.TrimPrefix(strings.ToUpper(strings.TrimSpace(s)), "SIG")
				continue
			}
			node[k] = bareSignals(child)
		}
	case []any:
		for i, child := range node {
			node[i] = bareSignals(child)
		}
	}
	return v
}
