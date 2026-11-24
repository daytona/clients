// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestIntegerFieldRejectsInexactInt64Responses(t *testing.T) {
	for _, literal := range []string{
		"-9223372036854775809", "-9223372036854775809.0", "-9.223372036854775809e18",
		"9223372036854775808", "9223372036854775808.0", "9.223372036854775808e18",
		"3.0000000000000000001", "-9223372036854775808.1", "1e-1000000000",
	} {
		t.Run(literal, func(t *testing.T) {
			// Given an int64 response field whose exact value is invalid.
			body := networkPolicyFixture()
			body["desired_revision"] = JSONValue(literal)
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/network-policy": policyResponse(t, body),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.GetNetworkPolicy(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Contains(t, err.Error(), "desired_revision")
		})
	}
}

func TestIntegerFieldPreservesExactInt64Responses(t *testing.T) {
	for _, tc := range []struct {
		literal string
		want    int64
	}{
		{"3.0", 3},
		{"3e0", 3},
		{"300e-2", 3},
		{"9007199254740993.0", 9007199254740993},
		{"9223372036854775807", math.MaxInt64},
		{"9223372036854775807.0", math.MaxInt64},
		{"9.223372036854775807e18", math.MaxInt64},
		{"-9223372036854775808", math.MinInt64},
		{"-9223372036854775808.0", math.MinInt64},
		{"-9.223372036854775808e18", math.MinInt64},
		{" 9223372036854775807 ", math.MaxInt64},
		{"0e-1000000000", 0},
		{"-0.0", 0},
	} {
		t.Run(tc.literal, func(t *testing.T) {
			// Given
			body := networkPolicyFixture()
			body["desired_revision"] = JSONValue(tc.literal)
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/network-policy": policyResponse(t, body),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			info, err := sandbox.GetNetworkPolicy(t.Context())
			// Then
			require.NoError(t, err)
			require.Equal(t, tc.want, info.DesiredRevision)
		})
	}
}
