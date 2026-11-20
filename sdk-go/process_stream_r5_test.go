// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestStreamError_whenNoHTTPFailureKeepsCodeWithoutStatus(t *testing.T) {
	// Given an HTTP-successful stream reporting a daemon error in-band.
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"GET /processes/p/logs": processSSEHandler(t,
			"event: error\ndata: {\"message\":\"gone\",\"code\":\"PROCESS_NOT_FOUND\"}\n\n"),
	})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)

	// When consuming the in-band error.
	var failure error
	for _, streamErr := range handle.StreamLogs(t.Context(), nil) {
		failure = streamErr
	}

	// Then the catalog still identifies its category, without inventing HTTP status.
	require.ErrorIs(t, failure, ErrProcessNotFound)
	require.ErrorIs(t, failure, ErrNotFound)
	var sdk *Error
	require.ErrorAs(t, failure, &sdk)
	require.Zero(t, sdk.StatusCode)
	require.NotContains(t, failure.Error(), "HTTP")
}
