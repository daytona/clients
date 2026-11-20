// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSandboxNetworkRejectsMalformedFields(t *testing.T) {
	for field, invalid := range map[string][]any{
		"desired_revision": {nil, true, 1.5, "1"},
		"applied_revision": {false, 1.5},
		"desired":          {[]string{}, "policy"},
		"applied":          {false},
	} {
		for index, value := range invalid {
			t.Run(field+"/"+string(rune('a'+index)), func(t *testing.T) {
				// Given
				body := networkPolicyFixture()
				body[field] = value
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /sandboxes/sbx/network-policy": policyResponse(t, body),
				})
				sandbox := newMutatorSandbox(t, fake)
				// When
				_, err := sandbox.GetNetworkPolicy(t.Context())
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
			})
		}
	}
}

func TestSandboxNetworkRejectsMalformedPolicy(t *testing.T) {
	for field, invalid := range map[string][]any{
		"version":       {nil, true, 1.5},
		"block_all":     {nil, "true", 1},
		"allow_cidrs":   {nil, "one", []any{nil}, []any{1}},
		"allow_domains": {"one", []any{nil}, []any{true}},
	} {
		for index, value := range invalid {
			t.Run(field+"/"+string(rune('a'+index)), func(t *testing.T) {
				// Given
				body := networkPolicyFixture()
				desired, ok := body["desired"].(map[string]any)
				require.True(t, ok)
				desired[field] = value
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /sandboxes/sbx/network-policy": policyResponse(t, body),
				})
				sandbox := newMutatorSandbox(t, fake)
				// When
				_, err := sandbox.GetNetworkPolicy(t.Context())
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
			})
		}
	}
}

func TestSandboxNetworkRequiresObjectAndETag(t *testing.T) {
	for name, body := range map[string]any{"object": networkPolicyFixture(), "array": []string{}, "null": nil} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx/network-policy": jsonHandler(t, body)})
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.GetNetworkPolicy(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestSandboxNetworkRejectsOptionalStringsOutsideSchema(t *testing.T) {
	// Given: optional fields must be strings or null under the response schema.
	body := networkPolicyFixture()
	body["status"], body["error"], body["applied_start_id"] = 1, []string{}, true
	body["desired_revision"] = 7.0
	body["applied_revision"] = nil
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx/network-policy": policyResponse(t, body)})
	sandbox := newMutatorSandbox(t, fake)
	// When
	_, err := sandbox.GetNetworkPolicy(t.Context())
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
}
