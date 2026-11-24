// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestNetworkPolicyRejectsMalformedOptionalStrings(t *testing.T) {
	for _, field := range []string{"applied_start_id", "status", "error"} {
		for _, value := range []JSONValue{JSONValue(`12`), JSONValue(`false`), JSONValue(`[]`), JSONValue(`{}`)} {
			t.Run(fmt.Sprintf("%s/%s", field, value), func(t *testing.T) {
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
				require.Contains(t, err.Error(), field)
			})
		}
	}
}

func TestNetworkPolicyPreservesOptionalStrings(t *testing.T) {
	for _, value := range []JSONValue{nil, JSONValue(`null`), JSONValue(`""`), JSONValue(`"future-value"`)} {
		t.Run(string(value), func(t *testing.T) {
			// Given
			body := networkPolicyFixture()
			body["desired_revision"] = JSONValue("7.0")
			body["applied_revision"] = nil
			for _, field := range []string{"applied_start_id", "status", "error"} {
				delete(body, field)
				if value != nil {
					body[field] = value
				}
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/network-policy": policyResponse(t, body),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			info, err := sandbox.GetNetworkPolicy(t.Context())
			// Then
			require.NoError(t, err)
			require.Equal(t, int64(7), info.DesiredRevision)
			require.Nil(t, info.AppliedRevision)
			want := ""
			if string(value) == `"future-value"` {
				want = "future-value"
			}
			require.Equal(t, want, info.AppliedStartID)
			require.Equal(t, want, info.Status)
			require.Equal(t, want, info.Error)
		})
	}
}
