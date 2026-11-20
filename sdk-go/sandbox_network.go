// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
)

// GetNetworkPolicy reads desired and applied policy state, including its ETag.
func (s *Sandbox) GetNetworkPolicy(ctx context.Context) (*NetworkPolicyInfo, error) {
	return s.networkPolicyRequest(ctx, apiRequest{})
}

// UpdateNetworkPolicy replaces the desired policy using the last read's ETag.
// Invalid policy combinations return ErrBadRequest before I/O. A stale ETag
// returns ErrStaleNetworkPolicyRevision, without retrying the update.
func (s *Sandbox) UpdateNetworkPolicy(
	ctx context.Context, spec NetworkPolicySpec, opts *IfMatchOptions,
) (*NetworkPolicyInfo, error) {
	if err := sandboxIfMatch(opts); err != nil {
		return nil, err
	}
	body, err := networkPolicyBody(spec)
	if err != nil {
		return nil, err
	}
	return s.networkPolicyRequest(ctx, apiRequest{
		Method: http.MethodPut, Body: body, Headers: http.Header{"If-Match": {opts.IfMatch}},
	})
}

// ResetNetworkPolicy removes the sandbox-specific policy using the last read's
// ETag. The API applies its default policy. A stale ETag returns
// ErrStaleNetworkPolicyRevision, without retrying.
func (s *Sandbox) ResetNetworkPolicy(ctx context.Context, opts *IfMatchOptions) (*NetworkPolicyInfo, error) {
	if err := sandboxIfMatch(opts); err != nil {
		return nil, err
	}
	return s.networkPolicyRequest(ctx, apiRequest{
		Method: http.MethodPut, Body: JSONValue("null"), Headers: http.Header{"If-Match": {opts.IfMatch}},
	})
}

func (s *Sandbox) networkPolicyRequest(ctx context.Context, request apiRequest) (*NetworkPolicyInfo, error) {
	segment, err := pathSegment(s.id)
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	var headers http.Header
	request.Path = "/sandboxes/" + segment + "/network-policy"
	request.Result, request.ResponseHeaders = &raw, &headers
	if requestErr := s.client.api().do(ctx, request); requestErr != nil {
		return nil, requestErr
	}
	return parseNetworkPolicyInfo(raw, headers.Get("ETag"))
}
