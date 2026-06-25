// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package tools

import "go.daytona.io/cli/apiclient"

var daytonaMCPHeaders map[string]string = map[string]string{
	apiclient.DaytonaSourceHeader: "daytona-mcp",
}
