// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func sandboxMetricsFixture() map[string]any {
	return map[string]any{
		"timestamp": "2026-11-20T14:00:00.123456Z", "cpuCount": 2, "cpuUtilization": 0.25,
		"memoryUsageBytes": 100, "memoryTotalBytes": 1000, "diskUsageBytes": 250, "diskTotalBytes": 2000,
	}
}

func TestSandboxMetrics(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /toolbox/sbx/system/metrics": jsonHandler(t, sandboxMetricsFixture())})
	sandbox := newMutatorSandbox(t, fake)
	// When
	metrics, err := sandbox.Metrics(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, &SystemMetrics{
		Timestamp: time.Date(2026, 11, 20, 14, 0, 0, 123456000, time.UTC),
		CPUCount:  2, CPUUtilization: 0.25, MemoryUsageBytes: 100, MemoryTotalBytes: 1000,
		DiskUsageBytes: 250, DiskTotalBytes: 2000,
	}, metrics)
}

func TestSandboxMetricsRejectsMalformedFields(t *testing.T) {
	for field, invalid := range map[string][]any{
		"timestamp":        {nil, "invalid", 1},
		"cpuCount":         {nil, -1, 1.5, true},
		"cpuUtilization":   {nil, -0.1, 1.1, "0.1"},
		"memoryUsageBytes": {nil, -1, 1.5},
		"memoryTotalBytes": {nil, -1, 1.5},
		"diskUsageBytes":   {nil, -1, 1.5},
		"diskTotalBytes":   {nil, -1, 1.5},
	} {
		for index, value := range invalid {
			t.Run(field+"/"+string(rune('a'+index)), func(t *testing.T) {
				// Given
				body := sandboxMetricsFixture()
				body[field] = value
				fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /toolbox/sbx/system/metrics": jsonHandler(t, body)})
				sandbox := newMutatorSandbox(t, fake)
				// When
				_, err := sandbox.Metrics(t.Context())
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
			})
		}
	}
}
