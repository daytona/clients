// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"math"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestMacModelR3CatalogNamesMalformedModelField(t *testing.T) {
	for _, raw := range []string{`{}`, `{"model":null}`, `{"model":false}`, `{"model":[]}`, `{"model":"wrong"}`} {
		t.Run(raw, func(t *testing.T) {
			// Given a malformed model in an otherwise valid catalog envelope.
			fixture := `{"regions":[{"id":"region-a","default_model":"m4","models":[` + raw + `]}]}`
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /mac-catalog": jsonHandler(t, json.RawMessage(fixture)),
			})
			client := newTestClient(t, fake.Server)
			// When reading the public catalog.
			_, err := client.MacModels().Catalog(t.Context(), nil)
			// Then the malformed-response error identifies the nested field.
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, "response field model")
		})
	}
}

func TestMacModelR3ListPreservesTypeScriptIntegerRange(t *testing.T) {
	// Given capacities outside int32, accepted by TypeScript's integer boundary.
	fixture := macModelFixture()
	fixture["cores"] = int64(math.MaxInt32) + 1
	fixture["default_size"] = map[string]int64{
		"cores": int64(math.MinInt32) - 1, "memory_mib": 8192, "disk_mib": 117760,
	}
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /mac-models": jsonHandler(t, map[string]any{"mac_models": []any{fixture}}),
	})
	client := newTestClient(t, fake.Server)
	// When reading models.
	models, err := client.MacModels().List(t.Context())
	// Then values remain intact rather than acquiring a Go-only int32 limit.
	require.NoError(t, err)
	require.Equal(t, int64(math.MaxInt32)+1, models[0].Cores)
	require.Equal(t, int64(math.MinInt32)-1, models[0].DefaultSize.Cores)
}
