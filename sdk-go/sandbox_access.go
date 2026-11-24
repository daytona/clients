// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"math"
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
	Ports []int
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
	return parseSandboxAccessToken(raw, true)
}

func parseSandboxAccessToken(raw JSONValue, strictExpiry bool) (*SandboxAccessToken, error) {
	object, err := parseObject(raw, "sandbox access token")
	if err != nil {
		return nil, badRequest("sandbox access token response is not a JSON object")
	}
	result := &SandboxAccessToken{}
	result.AccessToken, err = sandboxTokenString(object, "access_token")
	if err != nil {
		return nil, err
	}
	result.TokenType, err = sandboxTokenString(object, "token_type")
	if err != nil {
		return nil, err
	}
	expiry, expiryErr := sandboxTokenString(object, "expires_at")
	if expiryErr != nil {
		return nil, expiryErr
	}
	result.Operations, err = sandboxStringList(object, "operations", true)
	if err != nil {
		return nil, err
	}
	result.Ports, err = sandboxTokenPorts(object)
	if err != nil {
		return nil, err
	}
	if result.AccessToken == "" {
		return nil, badRequest("sandbox access token response has an empty access_token")
	}
	result.ExpiresAt, err = parseTimestamp(expiry)
	if err != nil {
		// Internal mints use the token until a 401 when only expiry metadata is
		// malformed, matching sandboxAuth.ts mintNow; delegated tokens are strict.
		if strictExpiry {
			return nil, badRequest("sandbox access token response has an expires_at that is not an RFC 3339 timestamp")
		}
		result.ExpiresAt = time.Time{}
	}
	return result, nil
}

func sandboxTokenString(object jsonObject, name string) (string, error) {
	if !present(object[name]) {
		return "", badRequest("sandbox access token response is missing its " + name)
	}
	value, err := field[string](object, name, true)
	if err != nil {
		return "", badRequest("sandbox access token response has a " + name + " that is not a string")
	}
	return value, nil
}

func sandboxTokenPorts(object jsonObject) ([]int, error) {
	ports, err := field[[]JSONValue](object, "ports", false)
	if err != nil {
		return nil, err
	}
	var result []int
	if ports != nil {
		result = make([]int, len(ports))
		for index, port := range ports {
			value, parseErr := integerField(jsonObject{"ports": port}, "ports", true)
			if parseErr != nil {
				return nil, parseErr
			}
			if value < 0 || value > math.MaxInt32 {
				return nil, badRequest("response field ports must be a non-negative int32")
			}
			result[index] = int(value)
		}
	}
	return result, nil
}
