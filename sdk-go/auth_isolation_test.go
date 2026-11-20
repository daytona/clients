// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestAuth_whenShortWaiterTimesOut_preservesConcurrentExchange(t *testing.T) {
	for _, test := range []struct {
		name     string
		transfer bool
	}{{"ordinary", false}, {"transfer", true}} {
		t.Run(test.name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given: both calls share a token exchange with a longer budget.
				entered, release := make(chan struct{}), make(chan struct{})
				var exchanges atomic.Int32
				transport := waitTestTransport{handler: func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path == "/auth/token" {
						if exchanges.Add(1) == 1 {
							close(entered)
						}
						select {
						case <-release:
							writeJSON(t, w, map[string]any{"access_token": "shared", "expires_in": 300})
						case <-r.Context().Done():
						}
						return
					}
					writeJSON(t, w, struct{}{})
				}}
				server := &httptest.Server{URL: "http://auth.example"}
				client := newTestClient(t, server, WithJWTToken("jwt"), WithProjectID("p"),
					WithHTTPClient(&http.Client{Transport: transport}), WithRequestTimeout(time.Minute))
				shortDone, longDone := make(chan error, 1), make(chan error, 1)

				// When: the initiating call expires while another caller waits.
				go func() {
					shortDone <- client.api().do(t.Context(), apiRequest{
						Path: "/", Timeout: time.Second, Transfer: test.transfer,
					})
				}()
				<-entered
				go func() {
					longDone <- client.api().do(t.Context(), apiRequest{Path: "/", Timeout: 10 * time.Second})
				}()
				synctest.Wait()
				shortErr := <-shortDone
				close(release)

				// Then: only the short waiter fails; the shared result serves the other.
				require.ErrorIs(t, shortErr, ErrClientTimeout)
				require.NoError(t, <-longDone)
				require.EqualValues(t, 1, exchanges.Load())
			})
		})
	}
}
