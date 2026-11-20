// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
)

func (u *contextUploader) putParts(ctx context.Context, parts []contextUploadPart) error {
	partsCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	var next atomic.Int64
	var first sync.Once
	var failure error
	var workers sync.WaitGroup
	for range min(contextUploadWorkers, len(parts)) {
		workers.Go(func() {
			for partsCtx.Err() == nil {
				index := next.Add(1) - 1
				if index >= int64(len(parts)) {
					return
				}
				if err := u.putPart(partsCtx, parts[index]); err != nil {
					first.Do(func() {
						failure = err
						cancel()
					})
					return
				}
			}
		})
	}
	workers.Wait()
	if failure != nil {
		return failure
	}
	if ctx.Err() != nil {
		return transportError(ctx, ctx.Err())
	}
	return nil
}

func (u *contextUploader) putPart(ctx context.Context, part contextUploadPart) error {
	var failure error
	for attempt := range contextUploadAttempts {
		if ctx.Err() != nil {
			return transportError(ctx, ctx.Err())
		}
		var retry bool
		retry, failure = u.putAttempt(ctx, part)
		if failure != nil && ctx.Err() != nil {
			return transportError(ctx, ctx.Err())
		}
		if !retry || attempt+1 == contextUploadAttempts {
			return failure
		}
		if err := u.pause(ctx, (500*time.Millisecond)<<attempt); err != nil {
			return err
		}
	}
	return failure
}

func (u *contextUploader) putAttempt(ctx context.Context, part contextUploadPart) (retry bool, failure error) {
	requestCtx, scope := newRequestScope(ctx, u.client, u.client.config.timeout)
	defer scope.close()
	scope.kick()
	body, err := u.openPart(scope, part)
	if err != nil {
		return false, err
	}
	defer func() {
		if closeErr := body.Close(); closeErr != nil && failure == nil {
			failure = wrapError(closeErr, "close build context archive")
			retry = false
		}
	}()
	request, err := http.NewRequestWithContext(requestCtx, http.MethodPut, part.url, body)
	if err != nil {
		return false, badRequest("build context upload response has an invalid storage URL")
	}
	request.ContentLength = part.bytes
	// The core HTTP client owns redirect/cookie policy and the caller's transport.
	// Authentication and API attribution live above it and never reach storage.
	response, err := u.client.http.Do(request)
	if err != nil {
		return u.partFailure(scope, part, err)
	}
	status := response.StatusCode
	// Only the status is needed; never buffer storage bodies or expose their text.
	if closeErr := response.Body.Close(); closeErr != nil {
		return u.partFailure(scope, part, closeErr)
	}
	switch {
	case status >= 200 && status < 300:
		return false, nil
	case status >= 300 && status < 400:
		return false, newSDKError("", status, "HTTP redirects are not supported")
	case status < 500 && status != 408 && status != 429:
		return false, badRequest(fmt.Sprintf("storage refused build context part %d (HTTP %d)", part.number, status))
	default:
		return true, newSDKError("SERVICE_UNAVAILABLE", 503,
			fmt.Sprintf("storage did not accept build context part %d (HTTP %d)", part.number, status))
	}
}

func (u *contextUploader) partFailure(
	scope *requestScope, part contextUploadPart, cause error,
) (bool, error) {
	if errors.Is(context.Cause(scope.ctx), ErrClientClosed) {
		return false, closedError()
	}
	var network net.Error
	if errors.Is(context.Cause(scope.ctx), context.DeadlineExceeded) ||
		(errors.As(cause, &network) && network.Timeout()) {
		failure := newSDKError("REQUEST_TIMEOUT", 408,
			fmt.Sprintf("build context part %d made no progress", part.number))
		failure.Err = context.DeadlineExceeded
		failure.category = ErrClientTimeout
		return true, failure
	}
	if scope.ctx.Err() != nil {
		return false, transportError(scope.ctx, scope.ctx.Err())
	}
	if err := u.checkArchive(); err != nil {
		return false, err
	}
	// HTTP transport errors embed presigned credentials; discard them entirely.
	failure := newSDKError("", 0, fmt.Sprintf("build context part %d could not reach storage", part.number))
	failure.category = ErrConnection
	return true, failure
}
