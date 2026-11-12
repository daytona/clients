// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"strconv"
	"strings"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/output"
)

func processKV(proc tbgen.ProcessDto) [][2]string {
	kv := [][2]string{{"id", proc.Id}, {"kind", string(proc.Kind)}, {"state", string(proc.State)}}
	if proc.Name != nil && *proc.Name != "" {
		kv = append(kv, [2]string{"name", *proc.Name})
	}
	if proc.Pid != nil {
		kv = append(kv, [2]string{"pid", strconv.Itoa(int(*proc.Pid))})
	}
	if proc.SessionId != nil && *proc.SessionId != "" {
		kv = append(kv, [2]string{"session_id", *proc.SessionId})
	}
	kv = append(kv, [2]string{"created_at", proc.CreatedAt}, [2]string{"cwd", proc.Cwd})
	if proc.State == tbgen.StateTerminal {
		if proc.ExitCode != nil {
			kv = append(kv, [2]string{"exit_code", strconv.Itoa(int(*proc.ExitCode))})
		}
		if proc.Reason != nil {
			kv = append(kv, [2]string{"exit_reason", string(*proc.Reason)})
		}
		if proc.Signal != nil && *proc.Signal != "" {
			kv = append(kv, [2]string{"signal", *proc.Signal})
		}
		if proc.ExitedAt != nil && *proc.ExitedAt != "" {
			kv = append(kv, [2]string{"exited_at", *proc.ExitedAt})
		}
	}
	return kv
}

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
