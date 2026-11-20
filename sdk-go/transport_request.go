// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strconv"
)

func (t *transport) open(ctx context.Context, spec apiRequest, streaming bool) (*http.Response, error) {
	if err := t.owner.ensureOpen(); err != nil {
		return nil, err
	}
	if _, err := relativeURL(t.baseURL, spec.Path, spec.Query); err != nil {
		return nil, err
	}
	if spec.Timeout < 0 && spec.Timeout != NoTimeout {
		return nil, badRequest("invalid request timeout")
	}
	if spec.MaxResponseBytes < NoLimit || spec.MaxResponseBytes > maxSafeInteger {
		return nil, badRequest("invalid MaxResponseBytes")
	}
	budget := t.owner.config.timeout
	if spec.Timeout != 0 {
		budget = boundedTimeout(spec.Timeout)
	}
	requestCtx, scope := newRequestScope(ctx, t.owner, budget)
	scope.kick()
	response, err := t.authorized(requestCtx, scope, spec)
	if err != nil {
		scope.close()
		return nil, err
	}
	if streaming || spec.Transfer {
		scope.pause()
	}
	response.Body = &responseBody{ReadCloser: response.Body, scope: scope, idle: spec.Transfer}
	return response, nil
}

func (t *transport) authorized(ctx context.Context, scope *requestScope, spec apiRequest) (*http.Response, error) {
	token, err := t.auth.bearer(ctx)
	if err != nil {
		return nil, transportError(ctx, err)
	}
	attemptSpec := spec
	attemptSpec.Headers = spec.Headers.Clone()
	if attemptSpec.Headers == nil {
		attemptSpec.Headers = make(http.Header)
	}
	for attempt := range 2 {
		if token != "" {
			attemptSpec.Headers.Set("Authorization", "Bearer "+token)
		}
		response, sendErr := t.send(ctx, scope, attemptSpec)
		if sendErr != nil {
			return nil, sendErr
		}
		if response.StatusCode >= 200 && response.StatusCode < 300 {
			return response, nil
		}
		failure := t.responseFailure(ctx, response, spec.MaxResponseBytes)
		if response.StatusCode != 401 || attempt != 0 {
			return nil, failure
		}
		if spec.Transfer {
			scope.kick()
		}
		fresh, refreshErr := t.auth.unauthorized(ctx, token)
		if refreshErr != nil {
			return nil, transportError(ctx, refreshErr)
		}
		replayable := spec.BodyReader == nil || spec.BodyFactory != nil
		if fresh == "" || !replayable {
			return nil, failure
		}
		token = fresh
	}
	return nil, newSDKError("UNAUTHORIZED", 401, "authentication failed")
}

func (t *transport) send(ctx context.Context, scope *requestScope, spec apiRequest) (*http.Response, error) {
	request, err := t.makeRequest(ctx, spec)
	if err != nil {
		return nil, err
	}
	if spec.Transfer {
		scope.kick()
		if request.Body != nil {
			request.Body = newTransferCancelBody(ctx, &uploadBody{ReadCloser: request.Body, scope: scope})
		}
	}
	response, err := t.owner.http.Do(request)
	if err != nil {
		return nil, transportError(ctx, err)
	}
	// Error bodies are bounded by the same deadline/idle timer as success bodies.
	if spec.Transfer {
		response.Body = &idleReadCloser{ReadCloser: response.Body, scope: scope}
	}
	return response, nil
}

func (t *transport) responseFailure(ctx context.Context, response *http.Response, limit int64) (failure error) {
	defer closeResponse(ctx, response, &failure)
	if response.StatusCode >= 300 && response.StatusCode < 400 {
		return errorFromResponse(response, nil)
	}
	body, err := t.readResponse(ctx, response, limit)
	if err != nil {
		var sdkError *Error
		if errors.As(err, &sdkError) && errors.Is(err, ErrPayloadTooLarge) {
			result := errorFromResponse(response, nil)
			if errors.As(result, &sdkError) {
				sdkError.Message = "HTTP " + strconv.Itoa(response.StatusCode) +
					": the response body was too large to read"
			}
			return result
		}
		return err
	}
	return errorFromResponse(response, body)
}

func (t *transport) readResponse(ctx context.Context, response *http.Response, override int64) ([]byte, error) {
	limit := t.owner.config.maxResponseBytes
	if override != 0 {
		limit = override
	}
	if limit > 0 && response.ContentLength > limit {
		return nil, responseTooLarge(limit)
	}
	var reader io.Reader = response.Body
	if limit > 0 {
		reader = io.LimitReader(reader, limit+1)
	}
	data, err := io.ReadAll(reader)
	if err != nil {
		return nil, transportError(ctx, err)
	}
	if limit > 0 && int64(len(data)) > limit {
		return nil, responseTooLarge(limit)
	}
	return data, nil
}

func responseTooLarge(limit int64) *Error {
	return newSDKError("PAYLOAD_TOO_LARGE", 413,
		"the response body exceeds the "+strconv.FormatInt(limit, 10)+"-byte WithMaxResponseBytes limit")
}

func errorsWithClose(err error, body io.Closer) error { return errors.Join(err, body.Close()) }

func closeResponse(ctx context.Context, response *http.Response, failure *error) {
	if err := response.Body.Close(); err != nil {
		*failure = errors.Join(*failure, transportError(ctx, err))
	}
}
