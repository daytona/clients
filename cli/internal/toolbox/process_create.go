// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"net/url"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/cerr"
)

// createStreamProcess retains a successful create response even if the caller
// is interrupted in flight. The stream returns that identity so its caller can
// signal the process rather than leave an untracked command running. Only the
// create is detached from cancellation, never from the caller's deadline:
// all subsequent streaming uses the original context.
func (c *Client) createStreamProcess(ctx context.Context, body tbgen.CreateProcessRequest, query url.Values) (tbgen.ProcessDto, error) {
	if err := cerr.FromContext(ctx); err != nil {
		return tbgen.ProcessDto{}, err
	}
	deadline := time.Now().Add(30 * time.Second)
	if callerDeadline, ok := ctx.Deadline(); ok && callerDeadline.Before(deadline) {
		deadline = callerDeadline
	}
	createCtx, cancel := context.WithDeadline(context.WithoutCancel(ctx), deadline)
	defer cancel()
	process, err := c.CreateProcess(createCtx, body, query)
	if err != nil {
		if contextErr := cerr.FromContext(createCtx); contextErr != nil {
			return process, contextErr
		}
	}
	return process, err
}
