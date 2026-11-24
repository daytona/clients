// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestR4ParserSearch_whenSeveralOptionsMalformed_reportsStableField(t *testing.T) {
	nan, unsafe := math.NaN(), int64(maxSafeInteger+1)
	outsideYear := time.Date(10000, 1, 1, 0, 0, 0, 0, time.UTC)
	for _, test := range []struct {
		name    string
		options SearchSandboxesOptions
		first   string
	}{
		{"text", SearchSandboxesOptions{
			Q: "\xff", NamePrefix: "\xff", IDPrefix: "\xff",
			ImageRef: "\xff", ImageRefPrefix: "\xff", ForkedFrom: "\xff",
		}, "q"},
		{"cores", SearchSandboxesOptions{MinCores: &nan, MaxCores: &nan}, "min_cores"},
		{"sizes", SearchSandboxesOptions{
			MinMemoryMiB: &unsafe, MaxMemoryMiB: &unsafe, MinDiskMiB: &unsafe, MaxDiskMiB: &unsafe,
		}, "min_memory_mib"},
		{"timestamps", SearchSandboxesOptions{
			CreatedStartAt: outsideYear, CreatedEndAt: outsideYear,
			UpdatedStartAt: outsideYear, UpdatedEndAt: outsideYear,
			LastActivityStartAt: outsideYear, LastActivityEndAt: outsideYear,
		}, "created_start_at"},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			for range 20 {
				// When
				_, err := client.Search(t.Context(), &test.options)
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				var sdkErr *Error
				require.ErrorAs(t, err, &sdkErr)
				require.Contains(t, sdkErr.Message, test.first)
			}
			require.Empty(t, fake.requests())
		})
	}
}
