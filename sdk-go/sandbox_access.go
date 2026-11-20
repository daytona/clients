// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"slices"
	"time"
)

// CreateAccessTokenOptions selects the scope and lifetime of a delegated token.
type CreateAccessTokenOptions struct {
	// Operations is nil for the server default; an empty slice grants none.
	// Unknown operations are rejected by the API, never silently narrowed.
	Operations []string
	// Ports is nil for the server default; an empty slice grants no ports.
	// Each port must be in 1..65535 and not reserved for the sandbox daemon.
	Ports []int
	// TTL is a positive whole-second lifetime. Zero uses the server default.
	// The API enforces its maximum lifetime.
	TTL time.Duration
}

// SandboxAccessToken is a scoped credential for one sandbox. It cannot refresh
// itself or mint further credentials. String and GoString omit its secret.
type SandboxAccessToken struct {
	// AccessToken is sensitive bearer material; do not log or share it publicly.
	AccessToken string
	// TokenType is the server token type, usually Bearer.
	TokenType string
	// ExpiresAt is the credential's expiration timestamp.
	ExpiresAt time.Time
	// Operations contains the granted operations, including future server values.
	Operations []string
	// Ports contains the granted ports; nil means unrestricted ports.
	Ports []int64
}

// String returns a credential-free token description.
func (t SandboxAccessToken) String() string {
	return "daytona.SandboxAccessToken(expires=" + t.ExpiresAt.Format(time.RFC3339Nano) + ")"
}

// GoString returns a credential-free token description.
func (t SandboxAccessToken) GoString() string { return t.String() }

// CreateAccessToken mints a delegated credential without changing this handle's
// own authentication. Nil options use server defaults. Invalid port numbers or
// a fractional or negative TTL return ErrBadRequest before I/O.
func (s *Sandbox) CreateAccessToken(ctx context.Context, opts *CreateAccessTokenOptions) (*SandboxAccessToken, error) {
	options := CreateAccessTokenOptions{}
	if opts != nil {
		options = *opts
	}
	var body struct {
		Operations *[]string `json:"operations,omitempty"`
		Ports      *[]int    `json:"ports,omitempty"`
		TTLSeconds int64     `json:"ttl_seconds,omitempty"`
	}
	if options.TTL < 0 || options.TTL%time.Second != 0 {
		return nil, badRequest("TTL must be a non-negative whole number of seconds")
	}
	body.TTLSeconds = int64(options.TTL / time.Second)
	if options.Operations != nil {
		operations := slices.Clone(options.Operations)
		body.Operations = &operations
	}
	if options.Ports != nil {
		ports := slices.Clone(options.Ports)
		for _, port := range ports {
			if err := validatePreviewPort(port); err != nil {
				return nil, err
			}
		}
		body.Ports = &ports
	}
	segment, err := pathSegment(s.id)
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	if requestErr := s.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: "/sandboxes/" + segment + "/access-token", Body: body, Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	return parseSandboxAccessToken(raw)
}

func parseSandboxAccessToken(raw JSONValue) (*SandboxAccessToken, error) {
	object, err := parseObject(raw, "sandbox access token")
	if err != nil {
		return nil, err
	}
	result := &SandboxAccessToken{}
	result.AccessToken, err = field[string](object, "access_token", true)
	if err != nil {
		return nil, err
	}
	if result.AccessToken == "" {
		return nil, badRequest("sandbox access token response has an empty access_token")
	}
	result.TokenType, err = field[string](object, "token_type", true)
	if err != nil {
		return nil, err
	}
	if _, expiryErr := field[string](object, "expires_at", true); expiryErr != nil {
		return nil, expiryErr
	}
	result.ExpiresAt, err = timestampField(object, "expires_at")
	if err != nil {
		return nil, err
	}
	result.Operations, err = sandboxStringList(object, "operations", true)
	if err != nil {
		return nil, err
	}
	ports, err := field[[]JSONValue](object, "ports", false)
	if err != nil {
		return nil, err
	}
	if ports != nil {
		result.Ports = make([]int64, len(ports))
		for index, port := range ports {
			value, parseErr := integerField(jsonObject{"ports": port}, "ports", true)
			if parseErr != nil {
				return nil, parseErr
			}
			result.Ports[index] = value
		}
	}
	return result, nil
}
