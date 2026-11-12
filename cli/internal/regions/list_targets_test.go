// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package regions

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestFanOut_queriesEachNormalizedURLOnce(t *testing.T) {
	// Given equivalent spellings of one API endpoint and a distinct endpoint.
	cat := &Catalog{Regions: []gen.Region{
		{Id: "east", ApiBaseUrl: str(" HTTPS://REGIONAL.EXAMPLE:443/// ")},
		{Id: "north", ApiBaseUrl: str("https://regional.example")},
		{Id: "west", ApiBaseUrl: str("https://other.example")},
	}}
	var calls atomic.Int32
	// When
	list, _ := FanOut(context.Background(), api.New("https://home", "k", "t"), cat, "", func(ctx context.Context, c *api.Client, region string) (Page[string], error) {
		calls.Add(1)
		return Page[string]{Items: []string{c.BaseURL}}, nil
	}, nil)
	// Then
	require.False(t, list.Incomplete)
	require.Equal(t, int32(2), calls.Load())
	require.Equal(t, []string{"https://regional.example", "https://other.example"}, list.Items)
	keys := cat.ListRegionKeys("https://home", "")
	require.Equal(t, map[string]string{"east": "east", "north": "east", "west": "west"}, keys)
}

func TestFanOut_selectedRegionKeepsItsCursorKey(t *testing.T) {
	// Given aliases sharing an API endpoint, with a different alias selected.
	cat := &Catalog{Regions: []gen.Region{
		{Id: "east", ApiBaseUrl: str("https://regional.example")},
		{Id: "north", ApiBaseUrl: str("https://regional.example/")},
	}}
	// When
	list, _ := FanOut(context.Background(), api.New("https://home", "k", "t"), cat, "NORTH", func(ctx context.Context, c *api.Client, region string) (Page[string], error) {
		return Page[string]{Items: []string{region}, NextCursor: "next"}, nil
	}, nil)
	// Then
	require.Equal(t, []string{"north"}, list.Items)
	require.Equal(t, "north:next", list.NextCursor)
	require.Equal(t, map[string]string{"east": "north", "north": "north"}, cat.ListRegionKeys("https://home", "NORTH"))
}

func TestBaseFor_normalizesFallbackAndPreservesPathCase(t *testing.T) {
	// Given
	r := gen.Region{ApiBaseUrl: str(" HTTP://[::1]:80/Api/// ")}
	// When
	base := BaseFor(r, "https://home")
	// Then
	require.Equal(t, "http://[::1]/Api", base)
	require.Equal(t, "https://home.example", BaseFor(gen.Region{}, " HTTPS://HOME.EXAMPLE:443/ "))
}

func TestFanOut_callerCancellationIsNotARegionFailure(t *testing.T) {
	cat := &Catalog{Regions: []gen.Region{
		{Id: "east", ApiBaseUrl: str("https://east.example")},
		{Id: "west", ApiBaseUrl: str("https://west.example")},
	}}
	for _, tc := range []struct {
		name string
		cat  *Catalog
	}{{"fan-out", cat}, {"no catalog", nil}} {
		t.Run(tc.name, func(t *testing.T) {
			// Given a caller that cancels while a region request is in flight.
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			// When
			list, err := FanOut(ctx, api.New("https://home", "k", "t"), tc.cat, "", func(ctx context.Context, c *api.Client, region string) (Page[string], error) {
				if c.BaseURL == "https://west.example" {
					return Page[string]{Items: []string{region}}, nil
				}
				cancel()
				return Page[string]{}, cerr.Wrap(cerr.ExitInterrupted, ctx.Err(), "interrupted")
			}, nil)
			// Then the interruption is returned, not a partial list (exit 7).
			require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
			require.False(t, list.Incomplete)
			require.Empty(t, list.Errors)
		})
	}
}

func TestFanOut_cancelledCallerIsNotReportedAsAnUnknownRegion(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	cat := &Catalog{Regions: []gen.Region{{Id: "east", ApiBaseUrl: str("https://east.example")}}}
	list, err := FanOut(ctx, api.New("https://home", "k", "t"), cat, "nope", func(context.Context, *api.Client, string) (Page[string], error) {
		return Page[string]{}, nil
	}, nil)
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
	require.False(t, list.Incomplete)
}

func TestFanOut_callerDeadlineIsATimeout(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Nanosecond)
	defer cancel()
	<-ctx.Done()
	_, err := FanOut(ctx, api.New("https://home", "k", "t"), nil, "", func(ctx context.Context, _ *api.Client, _ string) (Page[string], error) {
		return Page[string]{}, ctx.Err()
	}, nil)
	require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
}
