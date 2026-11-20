// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestMacModelsRejectMalformedModelFields(t *testing.T) {
	for name := range macModelFixture() {
		if name == "extra" {
			continue
		}
		for _, mutation := range []string{"missing", "type", "null"} {
			if name == "default_size" && mutation == "null" {
				continue
			}
			t.Run(name+"/"+mutation, func(t *testing.T) {
				// Given
				model := macModelFixture()
				switch mutation {
				case "missing":
					delete(model, name)
				case "type":
					model[name] = []any{}
				case "null":
					model[name] = nil
				}
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /mac-models": jsonHandler(t, map[string]any{"mac_models": []any{model}}),
				})
				service := newTestClient(t, fake.Server).MacModels()
				// When
				_, err := service.List(t.Context())
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.ErrorContains(t, err, name)
			})
		}
	}
}

func TestMacModelsRejectMalformedCatalog(t *testing.T) {
	for name, fixture := range map[string]any{
		"null": nil, "array": []any{}, "missing regions": map[string]any{},
		"null regions":            map[string]any{"regions": nil},
		"non object region":       map[string]any{"regions": []any{nil}},
		"missing id":              map[string]any{"regions": []any{map[string]any{"default_model": "model", "models": []any{}}}},
		"bad default":             map[string]any{"regions": []any{map[string]any{"id": "region", "default_model": 3, "models": []any{}}}},
		"missing models":          map[string]any{"regions": []any{map[string]any{"id": "region", "default_model": "model"}}},
		"null models":             map[string]any{"regions": []any{map[string]any{"id": "region", "default_model": "model", "models": nil}}},
		"non object model":        macCatalogWithEntry(nil),
		"missing model":           macCatalogWithEntry(map[string]any{"available": 1, "price": nil}),
		"fractional availability": macCatalogWithEntry(map[string]any{"model": macModelFixture(), "available": 1.5, "price": nil}),
		"missing price":           macCatalogWithEntry(map[string]any{"model": macModelFixture(), "available": 1}),
		"bad price":               macCatalogWithEntry(map[string]any{"model": macModelFixture(), "available": 1, "price": []any{}}),
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /mac-catalog": jsonHandler(t, fixture)})
			service := newTestClient(t, fake.Server).MacModels()
			// When
			_, err := service.Catalog(t.Context(), nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func macCatalogWithEntry(entry any) map[string]any {
	return map[string]any{"regions": []any{
		map[string]any{"id": "region", "default_model": "model", "models": []any{entry}},
	}}
}

func TestMacModelsRejectMalformedPriceFields(t *testing.T) {
	for name := range macPriceFixture() {
		for _, mutation := range []string{"missing", "type", "null"} {
			t.Run(name+"/"+mutation, func(t *testing.T) {
				// Given
				price := macPriceFixture()
				switch mutation {
				case "missing":
					delete(price, name)
				case "type":
					price[name] = []any{}
				case "null":
					price[name] = nil
				}
				fixture := macCatalogWithEntry(map[string]any{"model": macModelFixture(), "available": 1, "price": price})
				fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /mac-catalog": jsonHandler(t, fixture)})
				service := newTestClient(t, fake.Server).MacModels()
				// When
				_, err := service.Catalog(t.Context(), nil)
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.ErrorContains(t, err, name)
			})
		}
	}
}

func TestMacModelsRejectMalformedListEnvelopes(t *testing.T) {
	for _, fixture := range []any{
		nil,
		[]any{},
		true,
		map[string]any{},
		map[string]any{"mac_models": nil},
		map[string]any{"mac_models": map[string]any{}},
		map[string]any{"mac_models": []any{nil}},
		map[string]any{"mac_models": []any{[]any{}}},
		map[string]any{"mac_models": []any{"model"}},
	} {
		t.Run("envelope", func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /mac-models": jsonHandler(t, fixture)})
			service := newTestClient(t, fake.Server).MacModels()
			// When
			_, err := service.List(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestMacModelsRejectMalformedDefaultResources(t *testing.T) {
	for _, name := range []string{"cores", "memory_mib", "disk_mib"} {
		t.Run(name, func(t *testing.T) {
			// Given
			model := macModelFixture()
			model["default_size"] = map[string]any{"cores": 1, "memory_mib": 2, "disk_mib": 3, name: 1.5}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /mac-models": jsonHandler(t, map[string]any{"mac_models": []any{model}}),
			})
			service := newTestClient(t, fake.Server).MacModels()
			// When
			_, err := service.List(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, name)
		})
	}
}
