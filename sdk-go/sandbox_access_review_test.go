// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestAccessToken_whenGrantedPortOutsideContract_rejectsResponse(t *testing.T) {
	for _, port := range []int64{-1, math.MinInt32, math.MaxInt32 + 1} {
		// Given
		body := sandboxAccessFixture()
		body["ports"] = []int64{port}
		fake := newFakeServer(t, map[string]http.HandlerFunc{
			"POST /sandboxes/sbx/access-token": jsonHandler(t, body),
		})
		sandbox := newMutatorSandbox(t, fake)
		// When
		_, err := sandbox.CreateAccessToken(t.Context(), nil)
		// Then
		require.ErrorIs(t, err, ErrBadRequest)
	}
}

func TestAccessToken_whenResponseMalformed_matchesTokenDiagnostic(t *testing.T) {
	for _, tc := range []struct {
		name string
		body JSONValue
		want string
	}{
		{"null", JSONValue(`null`), "is not a JSON object"},
		{"array", JSONValue(`[]`), "is not a JSON object"},
		{"missing token", JSONValue(`{}`), "is missing its access_token"},
		{"null token", JSONValue(`{"access_token":null}`), "is missing its access_token"},
		{"wrong token", JSONValue(`{"access_token":5}`), "has a access_token that is not a string"},
		{"empty token", JSONValue(`{"access_token":"","token_type":"Bearer","expires_at":"2030-01-01T00:00:00Z","operations":[]}`), "has an empty access_token"},
		{"missing expiry", JSONValue(`{"access_token":"tok","token_type":"Bearer"}`), "is missing its expires_at"},
		{"wrong expiry", JSONValue(`{"access_token":"tok","token_type":"Bearer","expires_at":5}`), "has a expires_at that is not a string"},
		{"bad expiry", JSONValue(`{"access_token":"tok","token_type":"Bearer","expires_at":"bad","operations":[]}`), "has an expires_at that is not an RFC 3339 timestamp"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/sbx/access-token": jsonHandler(t, tc.body),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.CreateAccessToken(t.Context(), nil)
			// Then
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, "sandbox access token response "+tc.want, detail.Message)
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
