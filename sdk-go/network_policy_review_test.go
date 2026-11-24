// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestNetworkPolicy_whenNestedFieldMalformed_reportsQualifiedPath(t *testing.T) {
	for _, policy := range []string{"desired", "applied"} {
		for _, field := range []string{"version", "block_all", "allow_cidrs", "allow_domains"} {
			t.Run(policy+"/"+field, func(t *testing.T) {
				// Given
				body := networkPolicyFixture()
				body[policy] = map[string]any{
					"version": 1, "block_all": true, "allow_cidrs": []string{}, "allow_domains": []string{},
					field: "invalid",
				}
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /sandboxes/sbx/network-policy": policyResponse(t, body),
				})
				sandbox := newMutatorSandbox(t, fake)
				// When
				_, err := sandbox.GetNetworkPolicy(t.Context())
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.ErrorContains(t, err, policy+"."+field)
			})
		}
	}
}

func TestNetworkPolicy_whenSeveralFieldsMalformed_reportsStableOrder(t *testing.T) {
	for _, tc := range []struct {
		name string
		body map[string]any
		want string
	}{
		{"metadata", map[string]any{"applied_start_id": false, "status": false, "error": false}, "applied_start_id"},
		{"policies", map[string]any{"desired_revision": 1, "desired": false, "applied": false}, "desired"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/network-policy": policyResponse(t, tc.body),
			})
			sandbox := newMutatorSandbox(t, fake)
			for range 100 {
				// When
				_, err := sandbox.GetNetworkPolicy(t.Context())
				// Then
				require.ErrorContains(t, err, tc.want)
			}
		})
	}
}
