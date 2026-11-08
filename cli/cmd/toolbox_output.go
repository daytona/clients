// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/output"
)

// The toolbox API speaks camelCase; the CLI's JSON and YAML output is
// snake_case everywhere. These print a toolbox-derived value with its keys
// converted; tables are unchanged.

func printToolboxObject(a *app.App, v any, kv [][2]string) error {
	if a.Format == output.FormatTable {
		return a.Printer().Object(v, kv)
	}
	converted, err := output.SnakeCase(v)
	if err != nil {
		return err
	}
	return a.Printer().Object(converted, kv)
}

func printToolboxList[T any](a *app.App, l output.List[T], tbl func([]T) output.Table) error {
	if a.Format == output.FormatTable {
		return output.ListOut(a.Printer(), l, tbl)
	}
	if l.Items == nil {
		l.Items = []T{}
	}
	converted, err := output.SnakeCase(l)
	if err != nil {
		return err
	}
	return a.Printer().Object(converted, nil)
}
