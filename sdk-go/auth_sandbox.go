// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"sync/atomic"
	"time"
)

type sandboxCredential struct {
	sandbox         *Sandbox
	cache           tokenCache
	mintable        bool
	mintUnsupported atomic.Bool
}

func (s *sandboxCredential) bearer(ctx context.Context) (string, error) {
	current := s.cache.snapshot()
	if !s.mintable || s.cache.fresh(current) {
		return current.token, nil
	}
	if s.mintUnsupported.Load() {
		return s.compatBearer(ctx)
	}
	token, err := s.cache.resolve(ctx, "", s.mint)
	if err == nil {
		return token, nil
	}
	switch errorStatus(err) {
	case 405, 501:
		s.mintUnsupported.Store(true)
		return s.compatBearer(ctx)
	case 404:
		return s.sandbox.client.auth.bearer(ctx)
	}
	if current.token != "" && !current.expiresAt.IsZero() && s.cache.now().Before(current.expiresAt) {
		return current.token, nil
	}
	return "", err
}

func (s *sandboxCredential) compatBearer(ctx context.Context) (string, error) {
	current := s.cache.snapshot()
	if current.token != "" && (current.expiresAt.IsZero() || s.cache.now().Before(current.expiresAt)) {
		return current.token, nil
	}
	return s.sandbox.client.auth.bearer(ctx)
}

func (s *sandboxCredential) unauthorized(ctx context.Context, rejected string) (string, error) {
	if !s.mintable {
		return "", nil
	}
	if s.mintUnsupported.Load() {
		return s.apiRetry(ctx, rejected)
	}
	token, err := s.cache.resolve(ctx, rejected, s.mint)
	if err == nil {
		if token == rejected {
			return "", nil
		}
		return token, nil
	}
	switch errorStatus(err) {
	case 405, 501:
		s.mintUnsupported.Store(true)
		return s.apiRetry(ctx, rejected)
	case 404:
		return s.apiRetry(ctx, rejected)
	case 403:
		return "", err
	default:
		if ctx.Err() != nil {
			return "", transportError(ctx, err)
		}
		return "", nil // Preserve the actionable toolbox 401 when re-minting fails.
	}
}

func (s *sandboxCredential) apiRetry(ctx context.Context, rejected string) (string, error) {
	provider := s.sandbox.client.auth
	token, err := provider.bearer(ctx)
	if err != nil {
		return "", err
	}
	if token == rejected {
		token, err = provider.unauthorized(ctx, rejected)
		if err != nil {
			return "", err
		}
	}
	if token == "" || token == rejected {
		return "", nil
	}
	s.cache.mu.Lock()
	if s.cache.value.token == rejected {
		s.cache.value = cachedToken{}
	}
	s.cache.mu.Unlock()
	return token, nil
}

func errorStatus(err error) int {
	var sdkError *Error
	if errors.As(err, &sdkError) {
		return sdkError.StatusCode
	}
	return 0
}

func (s *sandboxCredential) mint(ctx context.Context) (cachedToken, error) {
	segment, err := pathSegment(s.sandbox.id)
	if err != nil {
		return cachedToken{}, err
	}
	var raw JSONValue
	request := apiRequest{
		Method: "POST", Path: "/sandboxes/" + segment + "/access-token", Body: struct{}{}, Result: &raw,
	}
	if requestErr := s.sandbox.client.api().do(ctx, request); requestErr != nil {
		return cachedToken{}, requestErr
	}
	object, err := parseObject(raw, "sandbox access token")
	if err != nil {
		return cachedToken{}, err
	}
	token, err := field[string](object, "access_token", true)
	if err != nil || token == "" {
		return cachedToken{}, badRequest("sandbox access token response has empty access_token")
	}
	if _, fieldErr := field[string](object, "token_type", true); fieldErr != nil {
		return cachedToken{}, fieldErr
	}
	if _, fieldErr := field[[]string](object, "operations", true); fieldErr != nil {
		return cachedToken{}, fieldErr
	}
	if _, fieldErr := field[string](object, "expires_at", true); fieldErr != nil {
		return cachedToken{}, fieldErr
	}
	if portsErr := checkTokenPorts(object); portsErr != nil {
		return cachedToken{}, portsErr
	}
	expiry, parseErr := timestampField(object, "expires_at")
	result := cachedToken{token: token}
	// A malformed expiry on an internally minted token is treated as absent.
	if parseErr == nil && !expiry.IsZero() {
		result.expiresAt, result.refreshAt = expiry, expiry.Add(-time.Minute)
	}
	return result, nil
}

func checkTokenPorts(object jsonObject) error {
	values, err := field[[]JSONValue](object, "ports", false)
	if err != nil {
		return err
	}
	for _, raw := range values {
		if _, fieldErr := integerField(jsonObject{"port": raw}, "port", true); fieldErr != nil {
			return fieldErr
		}
	}
	return nil
}
