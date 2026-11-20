// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"math"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestNetworkPolicyVersionInt32Bounds(t *testing.T) {
	for _, policy := range []string{"desired", "applied"} {
		for _, version := range []int64{math.MinInt32 - 1, math.MinInt32, math.MaxInt32, math.MaxInt32 + 1} {
			t.Run(fmt.Sprintf("%s/%d", policy, version), func(t *testing.T) {
				// Given
				body := networkPolicyFixture()
				body[policy] = map[string]any{"version": version, "block_all": true, "allow_cidrs": []string{}}
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /sandboxes/sbx/network-policy": policyResponse(t, body),
				})
				sandbox := newMutatorSandbox(t, fake)
				// When
				info, err := sandbox.GetNetworkPolicy(t.Context())
				// Then
				if version < math.MinInt32 || version > math.MaxInt32 {
					require.ErrorIs(t, err, ErrBadRequest)
					require.Contains(t, err.Error(), "version")
					return
				}
				require.NoError(t, err)
				got := info.Desired
				if policy == "applied" {
					got = info.Applied
				}
				require.Equal(t, version, got.Version)
			})
		}
	}
}
