// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"net/http"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestR4ParserSecrets_whenSeveralStringsMalformed_reportsStableField(t *testing.T) {
	// Given
	body := sandboxSecretsFixture()
	for _, name := range []string{"status", "error", "capture_status"} {
		body[name] = false
	}
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/secrets": policyResponse(t, body),
	})
	sandbox := newMutatorSandbox(t, fake)
	for range 20 {
		// When
		_, err := sandbox.GetSecrets(t.Context())
		// Then
		require.ErrorIs(t, err, ErrBadRequest)
		var sdkErr *Error
		require.ErrorAs(t, err, &sdkErr)
		require.Contains(t, sdkErr.Message, "field status")
	}
}

func TestR4ParserMetrics_whenSeveralIntegersMalformed_reportsStableField(t *testing.T) {
	// Given
	body := sandboxMetricsFixture()
	for _, name := range []string{"cpuCount", "memoryUsageBytes", "memoryTotalBytes", "diskUsageBytes", "diskTotalBytes"} {
		body[name] = false
	}
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /toolbox/sbx/system/metrics": jsonHandler(t, body),
	})
	sandbox := newMutatorSandbox(t, fake)
	for range 20 {
		// When
		_, err := sandbox.Metrics(t.Context())
		// Then
		require.ErrorIs(t, err, ErrBadRequest)
		var sdkErr *Error
		require.ErrorAs(t, err, &sdkErr)
		require.Contains(t, sdkErr.Message, "cpuCount")
	}
}

func TestR4ParserMetrics_whenCPUCountAtContractBounds(t *testing.T) {
	for _, count := range []int64{-1, 0, math.MaxInt32, math.MaxInt32 + 1} {
		t.Run(strconv.FormatInt(count, 10), func(t *testing.T) {
			// Given
			body := sandboxMetricsFixture()
			body["cpuCount"] = count
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /toolbox/sbx/system/metrics": jsonHandler(t, body),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			metrics, err := sandbox.Metrics(t.Context())
			// Then
			if count < 0 || count > math.MaxInt32 {
				require.ErrorIs(t, err, ErrBadRequest)
				require.Contains(t, err.Error(), "cpuCount")
				return
			}
			require.NoError(t, err)
			require.Equal(t, count, metrics.CPUCount)
		})
	}
}
