// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build windows

package toolbox

import "context"

// Windows has no SIGWINCH; the initial terminal geometry still applies.
func (c *Client) forwardTerminalResizes(_ context.Context, _ string) func() {
	return func() {}
}
