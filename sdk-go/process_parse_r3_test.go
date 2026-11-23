// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestProcessInfo_whenMultipleTimestampsMalformedReportsFirstField(t *testing.T) {
	// Given two malformed timestamps in one process response.
	fixture := processFixture("p")
	fixture["createdAt"], fixture["startedAt"] = "invalid", "invalid"
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p": jsonHandler(t, fixture),
	})
	for range 100 {
		// When reading the same malformed metadata repeatedly.
		_, err := sandbox.Process().Get(t.Context(), "p")
		// Then timestamp checks always start at createdAt.
		require.ErrorIs(t, err, ErrBadRequest)
		require.ErrorContains(t, err, "createdAt")
		require.NotContains(t, err.Error(), "startedAt")
	}
}
