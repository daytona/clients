// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import "go.daytona.io/cli/apiclient"

var daytonaMCPHeaders map[string]string = map[string]string{
	apiclient.DaytonaSourceHeader: "daytona-mcp",
}
