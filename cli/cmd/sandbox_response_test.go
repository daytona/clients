// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSandboxGet_toleratesUnknownResponseValues(t *testing.T) {
	for _, class := range []string{"unknown-class", "future-class"} {
		t.Run(class, func(t *testing.T) {
			// Given
			const id = "11111111-1111-1111-1111-111111111111"
			response := fmt.Sprintf(`{"id":%q,"class":%q,"status":"future-status","future_field":2}`, id, class)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = fmt.Fprint(w, response)
			}))
			defer server.Close()
			t.Setenv("DAYTONA_CONFIG_DIR", t.TempDir())
			var stdout, stderr bytes.Buffer
			root := NewRoot(&stdout, &stderr)
			root.SetArgs([]string{"--api-url", server.URL, "--api-key", "key", "-o", "json", "sandbox", "get", id})

			// When
			err := root.ExecuteContext(t.Context())

			// Then
			require.NoError(t, err)
			output := mustJSON(t, stdout.String())
			require.Equal(t, id, output["id"])
			require.Equal(t, class, output["class"])
			require.Equal(t, "future-status", output["status"])
			// The API's representation: a field it adds passes through, and
			// fields it left out stay absent instead of becoming zero values.
			require.Equal(t, float64(2), output["future_field"])
			for _, absent := range []string{"cpu", "memory_mib", "name", "port_map", "created_at"} {
				require.NotContains(t, output, absent)
			}
		})
	}
}
