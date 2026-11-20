// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestPortsPreviewLink_whenMintFailsWithUnusableRoute_returnsMintError(t *testing.T) {
	for _, tc := range []struct {
		name, endpointURL, routeURL, token string
	}{
		{"missing token", "https://endpoint.invalid", "https://route.invalid", ""},
		{"missing URL", "", "", "route-token"},
		{"missing both", "", "", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given a private route without usable credentials and a failing mint.
			endpoint := previewEndpointFixture()
			endpoint["url"] = tc.endpointURL
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/ports/8080": jsonHandler(t, endpoint),
				"POST /sandboxes/sbx/ports/8080/preview-url": jsonHandler(t,
					map[string]string{"url": tc.routeURL, "token": tc.token}),
				"POST /sandboxes/sbx/access-token": func(w http.ResponseWriter, _ *http.Request) {
					w.WriteHeader(http.StatusServiceUnavailable)
				},
			})
			sandbox := portsTestSandbox(t, fake)
			sandbox.auth.mintable = true
			// When obtaining a preview link.
			link, err := sandbox.Ports().PreviewLink(t.Context(), 8080)
			// Then a failed mint is not hidden behind an unusable link.
			require.ErrorIs(t, err, ErrServiceUnavailable)
			require.Nil(t, link)
		})
	}
}
