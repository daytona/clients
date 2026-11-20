// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/base64"
	"math"
	"net/http"
	"regexp"
	"strings"
	"time"
)

func (s *sandboxCredential) previewToken(ctx context.Context, port int) (cachedToken, error) {
	if !s.mintable || s.mintUnsupported.Load() {
		return cachedToken{}, nil
	}
	segment, err := pathSegment(s.sandbox.id)
	if err != nil {
		return cachedToken{}, err
	}
	body := struct {
		Operations []string `json:"operations"`
		Ports      []int    `json:"ports"`
	}{Operations: []string{}, Ports: []int{port}}
	var raw JSONValue
	if requestErr := s.sandbox.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: "/sandboxes/" + segment + "/access-token",
		Body: body, Result: &raw,
	}); requestErr != nil {
		switch errorStatus(requestErr) {
		case http.StatusMethodNotAllowed, http.StatusNotImplemented:
			s.mintUnsupported.Store(true)
		}
		return cachedToken{}, requestErr
	}
	return parsePreviewAccessToken(raw)
}

// Scoped token responses require a valid expiry, unlike the core's internally
// cached toolbox token refresh which intentionally tolerates an unknown expiry.
func parsePreviewAccessToken(raw JSONValue) (cachedToken, error) {
	object, err := parseObject(raw, "preview access token")
	if err != nil {
		return cachedToken{}, err
	}
	token, err := field[string](object, "access_token", true)
	if err != nil {
		return cachedToken{}, err
	}
	if token == "" {
		return cachedToken{}, badRequest("preview access token response has empty access_token")
	}
	if _, fieldErr := field[string](object, "token_type", true); fieldErr != nil {
		return cachedToken{}, fieldErr
	}
	operations, err := field[[]JSONValue](object, "operations", true)
	if err != nil {
		return cachedToken{}, err
	}
	for _, operation := range operations {
		if _, fieldErr := field[string](jsonObject{"operations": operation}, "operations", true); fieldErr != nil {
			return cachedToken{}, fieldErr
		}
	}
	if fieldErr := checkTokenPorts(object); fieldErr != nil {
		return cachedToken{}, fieldErr
	}
	if _, fieldErr := field[string](object, "expires_at", true); fieldErr != nil {
		return cachedToken{}, fieldErr
	}
	expiry, err := timestampField(object, "expires_at")
	if err != nil {
		return cachedToken{}, err
	}
	return cachedToken{token: token, expiresAt: expiry}, nil
}

// previewTokenExpiry reads only advisory JWT expiry, never permissions.
func previewTokenExpiry(token string) time.Time {
	matched, err := regexp.MatchString(`^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$`, token)
	if err != nil || !matched {
		return time.Time{}
	}
	parts := strings.Split(token, ".")
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil || checkJSON(payload) != nil {
		return time.Time{}
	}
	object, err := parseObject(payload, "JWT")
	if err != nil {
		return time.Time{}
	}
	exp, err := field[float64](object, "exp", true)
	if err != nil || exp <= 0 || math.IsInf(exp, 0) || math.IsNaN(exp) {
		return time.Time{}
	}
	ms := math.Floor(exp * 1000)
	// Match the TypeScript Date range; tokens outside it have unknown expiry.
	if math.IsInf(ms, 0) || ms > 8.64e15 {
		return time.Time{}
	}
	return time.UnixMilli(int64(ms)).UTC()
}
