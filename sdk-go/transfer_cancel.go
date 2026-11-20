// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"sync"
)

// The HTTP transport may wait for its write goroutine before closing the request
// body on cancellation. Closing a blocked producer from the request context
// breaks that cycle, including when the transfer's idle deadline expires.
type transferCancelBody struct {
	io.ReadCloser
	once   sync.Once
	detach func() bool
	err    error
}

func newTransferCancelBody(ctx context.Context, source io.ReadCloser) *transferCancelBody {
	body := &transferCancelBody{ReadCloser: source}
	body.detach = context.AfterFunc(ctx, body.closeSource)
	return body
}

func (b *transferCancelBody) closeSource() {
	b.once.Do(func() { b.err = b.ReadCloser.Close() })
}

func (b *transferCancelBody) Close() error {
	b.detach()
	b.closeSource()
	return b.err
}
