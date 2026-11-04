// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package workload

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/stretchr/testify/require"
)

func TestSandboxJSON_toleratesUnknownResponseValues(t *testing.T) {
	for _, class := range []string{"unknown-class", "future-class"} {
		t.Run(class, func(t *testing.T) {
			// Given
			body := fmt.Appendf(nil, `{"id":"sandbox-id","class":%q,"status":"future-status","future_field":2}`, class)
			var sandbox gen.Sandbox

			// When
			err := json.Unmarshal(body, &sandbox)

			// Then
			require.NoError(t, err)
			require.Equal(t, class, string(sandbox.Class))
			require.Equal(t, "future-status", string(sandbox.Status))
		})
	}
}

func TestResolveSandbox_toleratesUnknownResponseValues(t *testing.T) {
	for _, class := range []string{"unknown-class", "future-class"} {
		t.Run(class, func(t *testing.T) {
			// Given
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = fmt.Fprintf(w, `{"id":"sandbox-id","class":%q,"status":"future-status","future_field":2}`, class)
			}))
			defer server.Close()

			// When
			resolved, err := ResolveSandbox(context.Background(), api.New(server.URL, "key", "test"), NewCatalogCache(), "sandbox-id")

			// Then
			require.NoError(t, err)
			require.Equal(t, "sandbox-id", resolved.Detail.Id)
			require.Equal(t, class, string(resolved.Detail.Class))
			require.Equal(t, "future-status", string(resolved.Detail.Status))
		})
	}
}
