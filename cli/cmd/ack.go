// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/output"
)

// printAck prints the acknowledgement of a command that has no resource to
// print: the identifier under the field that names it plus a boolean named
// after the action, e.g. {"id": "img1", "deleted": true}. With table output
// a non-empty human message replaces the two rows (on stderr, as before).
func printAck(a *app.App, idField, id, action, human string) error {
	if human != "" && a.Format == output.FormatTable {
		a.Info("%s", human)
		return nil
	}
	return a.Printer().Object(map[string]any{idField: id, action: true}, [][2]string{{idField, id}, {action, "true"}})
}
