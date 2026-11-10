// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"net/url"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
)

// createStreamProcess retains a successful create response even if the caller
// is interrupted in flight. The stream returns that identity so its caller can
// signal the process rather than leave an untracked command running. Only the
// create is detached: all subsequent streaming uses the original context.
func (c *Client) createStreamProcess(ctx context.Context, body tbgen.CreateProcessRequest, query url.Values) (tbgen.ProcessDto, error) {
	if err := ctx.Err(); err != nil {
		return tbgen.ProcessDto{}, err
	}
	createCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 30*time.Second)
	defer cancel()
	return c.CreateProcess(createCtx, body, query)
}
