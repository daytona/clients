// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	stderrors "errors"
	"time"

	sdkerrors "github.com/daytona/clients/sdk-go/pkg/errors"
	"github.com/daytona/clients/sdk-go/pkg/options"
	toolbox "github.com/daytona/clients/toolbox-api-client-go"
)

const interpreterContextCleanupTimeout = 30 * time.Second

// InterpreterContext identifies an isolated interpreter context. Use it inside
// [CodeInterpreterService.WithContext]; create contexts manually only when their
// lifetime must span multiple callbacks.
type InterpreterContext = toolbox.InterpreterContext

// WithContext runs fn with a fresh interpreter context and always deletes it
// afterwards. Use it for scoped state; use CreateContext and DeleteContext when
// the context must outlive a single callback. Deletion is attempted even when
// ctx is already canceled, bounded by its own 30s timeout.
func (c *CodeInterpreterService) WithContext(ctx context.Context, fn func(InterpreterContext) error, opts ...func(*options.InterpreterContext)) (err error) {
	contextOpts := options.Apply(opts...)
	request := toolbox.NewCreateContextRequest()
	if contextOpts.Cwd != nil {
		request.SetCwd(*contextOpts.Cwd)
	}
	interpreterContext, httpResp, err := c.toolboxClient.InterpreterAPI.CreateInterpreterContext(ctx).Request(*request).Execute()
	if err != nil {
		return sdkerrors.ConvertToolboxError(err, httpResp)
	}
	defer func() {
		cleanupCtx, cancelCleanup := context.WithTimeout(context.WithoutCancel(ctx), interpreterContextCleanupTimeout)
		defer cancelCleanup()
		err = stderrors.Join(err, c.DeleteContext(cleanupCtx, interpreterContext.Id))
	}()
	return fn(*interpreterContext)
}
