// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func macModelFixture() map[string]any {
	return map[string]any{
		"id": "m4-10c-16g", "name": "Mac model", "cores": 10, "memory_mib": 16384,
		"disk_mib": 262144, "sandbox_disk_mib": 235520, "max_sandboxes": 2,
		"default_size": map[string]any{"cores": 5, "memory_mib": 8192, "disk_mib": 117760},
		"extra":        true,
	}
}

func macPriceFixture() map[string]any {
	return map[string]any{"cents_per_hour": 12.5, "period_cents": 300, "period_seconds": 86400, "currency": "USD"}
}

func macCatalogFixture() map[string]any {
	return map[string]any{"regions": []any{
		map[string]any{"id": "region-a", "default_model": "m4-10c-16g", "models": []any{
			map[string]any{"model": macModelFixture(), "available": 3, "price": macPriceFixture()},
			map[string]any{"model": macModelFixture(), "available": 0, "price": nil},
		}},
	}}
}

func expectedMacModel() MacModel {
	return MacModel{
		ID: "m4-10c-16g", Name: "Mac model", Cores: 10, MemoryMiB: 16384,
		DiskMiB: 262144, SandboxDiskMiB: 235520, MaxSandboxes: 2,
		DefaultSize: &MacResources{Cores: 5, MemoryMiB: 8192, DiskMiB: 117760},
	}
}

func TestMacModelsList(t *testing.T) {
	// Given: an unknown cursor must not turn this registry into a paginated list.
	fixture := map[string]any{"mac_models": []any{macModelFixture()}, "next_cursor": 42}
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /mac-models": jsonHandler(t, fixture)})
	service := newTestClient(t, fake.Server).MacModels()
	// When
	models, err := service.List(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, []MacModel{expectedMacModel()}, models)
	requests := fake.requests()
	require.Len(t, requests, 1)
	require.Empty(t, requests[0].Query)
	require.Empty(t, requests[0].Body)
}

func TestMacModelsCatalog(t *testing.T) {
	for _, region := range []string{"", "region-a", "a+b /,c"} {
		t.Run(region, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /mac-catalog": jsonHandler(t, macCatalogFixture())})
			service := newTestClient(t, fake.Server).MacModels()
			// When
			catalog, err := service.Catalog(t.Context(), &MacCatalogOptions{Region: region})
			// Then
			require.NoError(t, err)
			require.Equal(t, &MacCatalog{Regions: []MacCatalogRegion{{
				ID: "region-a", DefaultModel: "m4-10c-16g", Models: []MacCatalogModel{
					{Model: expectedMacModel(), Available: 3, Price: &MacPrice{
						CentsPerHour: 12.5, PeriodCents: 300, Period: 24 * time.Hour, Currency: "USD",
					}},
					{Model: expectedMacModel(), Available: 0},
				},
			}}}, catalog)
			requests := fake.requests()
			require.Len(t, requests, 1)
			require.Equal(t, region, requests[0].Query.Get("region_ids"))
			if region == "" {
				require.Empty(t, requests[0].Query)
			}
		})
	}
}

func TestMacModelsNullableDefaultSize(t *testing.T) {
	// Given
	fixture := macModelFixture()
	fixture["default_size"] = nil
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /mac-models": jsonHandler(t, map[string]any{"mac_models": []any{fixture}}),
	})
	service := newTestClient(t, fake.Server).MacModels()
	// When
	models, err := service.List(t.Context())
	// Then
	require.NoError(t, err)
	require.Nil(t, models[0].DefaultSize)
}

func TestMacModelsEmptyLists(t *testing.T) {
	for _, operation := range []string{"list", "catalog"} {
		t.Run(operation, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /mac-models":  jsonHandler(t, map[string]any{"mac_models": []any{}}),
				"GET /mac-catalog": jsonHandler(t, map[string]any{"regions": []any{}}),
			})
			service := newTestClient(t, fake.Server).MacModels()
			// When / Then
			if operation == "list" {
				models, err := service.List(t.Context())
				require.NoError(t, err)
				require.Empty(t, models)
				require.NotNil(t, models)
			} else {
				catalog, err := service.Catalog(t.Context(), nil)
				require.NoError(t, err)
				require.Empty(t, catalog.Regions)
				require.NotNil(t, catalog.Regions)
			}
		})
	}
}

func TestMacModelsRejectBadRegion(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	service := newTestClient(t, fake.Server).MacModels()
	// When
	_, err := service.Catalog(t.Context(), &MacCatalogOptions{Region: "\xff"})
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}

func TestMacModelsErrorsAndCancellation(t *testing.T) {
	for _, operation := range []string{"list", "catalog"} {
		for _, canceled := range []bool{false, true} {
			t.Run(operation, func(t *testing.T) {
				// Given
				handler := func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusForbidden)
					writeJSON(t, w, map[string]any{"code": "FEATURE_NOT_ENABLED", "source": "API", "message": "disabled"})
				}
				fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /mac-models": handler, "GET /mac-catalog": handler})
				service := newTestClient(t, fake.Server).MacModels()
				ctx, cancel := context.WithCancel(t.Context())
				defer cancel()
				if canceled {
					cancel()
				}
				// When
				var err error
				if operation == "list" {
					_, err = service.List(ctx)
				} else {
					_, err = service.Catalog(ctx, nil)
				}
				// Then
				if canceled {
					require.ErrorIs(t, err, context.Canceled)
					require.Empty(t, fake.requests())
				} else {
					require.ErrorIs(t, err, ErrFeatureNotEnabled)
					require.ErrorIs(t, err, ErrForbidden)
				}
			})
		}
	}
}
