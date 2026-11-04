// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"testing"

	"github.com/daytona/clients/cli/internal/output"
	"github.com/stretchr/testify/require"
)

func TestCollectPaged_reportsEachFailedRegionOnce(t *testing.T) {
	pages := map[string]output.List[int]{
		"":   {Items: []int{1}, NextCursor: "c1", Incomplete: true, Errors: []output.RegionError{{Region: "west", Message: "down"}}},
		"c1": {Items: []int{2}, NextCursor: "c2", Incomplete: true, Errors: []output.RegionError{{Region: "west", Message: "down"}}},
		"c2": {Items: []int{3}, Incomplete: true, Errors: []output.RegionError{{Region: "west", Message: "down"}}},
	}
	all, err := collectPaged(context.Background(), "", func(_ context.Context, cursor string) (output.List[int], error) {
		return pages[cursor], nil
	})
	require.NoError(t, err)
	require.Equal(t, []int{1, 2, 3}, all.Items)
	require.True(t, all.Incomplete)
	require.Equal(t, []output.RegionError{{Region: "west", Message: "down"}}, all.Errors)
}
