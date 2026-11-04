// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"testing"

	"github.com/getkin/kin-openapi/openapi3"
	"github.com/stretchr/testify/require"
)

func TestContractEnums_followDeclaredOpenness(t *testing.T) {
	for _, tc := range []struct {
		name, marker string
		acceptFuture bool
	}{
		{"open", "x-daytona-open-enum", true},
		{"exact", "x-daytona-exact-enum", false},
		{"unannotated", "", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given a string enum with the specified contract annotation.
			raw := map[string]any{"type": "string", "enum": []any{"known"}}
			if tc.marker != "" {
				raw[tc.marker] = true
			}
			// When the converted schema validates a future enum value.
			downconvert31(raw)
			data, err := json.Marshal(raw)
			require.NoError(t, err)
			var schema openapi3.Schema
			require.NoError(t, json.Unmarshal(data, &schema))
			err = schema.VisitJSON("future")
			// Then only an explicitly open enum accepts it, without losing its type.
			if tc.acceptFuture {
				require.NoError(t, err)
			} else {
				require.Error(t, err)
			}
			require.Error(t, schema.VisitJSON(42))
		})
	}
}
