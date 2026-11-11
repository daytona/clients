// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package output

import "fmt"

// Format is the value of --output.
type Format string

const (
	FormatTable Format = "table"
	FormatJSON  Format = "json"
	FormatYAML  Format = "yaml"
)

// ParseFormat accepts only the documented, case-sensitive output names.
func ParseFormat(s string) (Format, error) {
	switch s {
	case "table":
		return FormatTable, nil
	case "json":
		return FormatJSON, nil
	case "yaml", "yml":
		return FormatYAML, nil
	}
	return "", fmt.Errorf("unknown --output %q (want table|json|yaml; yml is an alias for yaml)", s)
}
