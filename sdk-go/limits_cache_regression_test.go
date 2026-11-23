// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestAccountLimitsRetriesMalformedOrganizationID(t *testing.T) {
	for _, id := range []string{" ", "\t", ".", ".."} {
		t.Run(id, func(t *testing.T) {
			// Given a malformed lookup followed by a valid response.
			fixture := accountKeyFixture()
			fixture["context"] = map[string]string{"org_id": id, "org_name": "Organization", "project_name": "Project"}
			lookups := 0
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /api-keys/current": func(w http.ResponseWriter, _ *http.Request) {
					lookups++
					if lookups == 1 {
						writeJSON(t, w, fixture)
						return
					}
					writeJSON(t, w, accountKeyFixture())
				},
				"GET /orgs/org-id/limits": jsonHandler(t, accountLimitsFixture()),
			})
			client := newTestClient(t, fake.Server)
			_, firstErr := client.Limits(t.Context(), nil)
			require.ErrorIs(t, firstErr, ErrBadRequest)
			// When
			result, err := client.Limits(t.Context(), nil)
			// Then
			require.NoError(t, err)
			require.EqualValues(t, 7, result.Sandboxes.Total.Usage)
		})
	}
}
