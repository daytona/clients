// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestCreatePreservesKey_whenClientClosesDuringPOST(t *testing.T) {
	for _, route := range createTestCases() {
		for _, key := range []string{"", "retry-after-close"} {
			t.Run(route.name+"/"+key, func(t *testing.T) {
				// Given: the server received the POST but has not answered it.
				arrived := make(chan struct{})
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST " + route.path: func(_ http.ResponseWriter, r *http.Request) {
						close(arrived)
						<-r.Context().Done()
					},
				})
				client := newTestClient(t, fake.Server, WithRequestTimeout(NoTimeout))
				result := make(chan error, 1)
				var sandbox *Sandbox
				go func() {
					var err error
					sandbox, err = route.call(t.Context(), client, &CreateFromSnapshotOptions{
						Wait: NoWait, IdempotencyKey: key,
					})
					result <- err
				}()
				<-arrived

				// When
				require.NoError(t, client.Close())
				err := <-result

				// Then: the uncertain outcome retains the actual key and closure.
				require.Nil(t, sandbox)
				require.ErrorIs(t, err, ErrClientClosed)
				var uncertain *SandboxCreateConnectionError
				require.ErrorAs(t, err, &uncertain)
				require.ErrorIs(t, err, ErrConnection)
				requests := fake.requests()
				require.Len(t, requests, 1)
				sentKey := requests[0].Header.Get("Idempotency-Key")
				require.NotEmpty(t, sentKey)
				require.Equal(t, sentKey, uncertain.IdempotencyKey)
				if key != "" {
					require.Equal(t, key, uncertain.IdempotencyKey)
				}
			})
		}
	}
}

func TestCreateReturnsAcceptedHandle_whenClientClosesDuringWait(t *testing.T) {
	for _, route := range createTestCases() {
		t.Run(route.name, func(t *testing.T) {
			// Given: creation was accepted and its observation is blocked.
			arrived := make(chan struct{})
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST " + route.path: jsonHandler(t, map[string]string{"id": "sbx", "status": "pending"}),
				"GET /sandboxes/sbx": func(_ http.ResponseWriter, r *http.Request) {
					close(arrived)
					<-r.Context().Done()
				},
			})
			client := newTestClient(t, fake.Server, WithRequestTimeout(NoTimeout))
			result := make(chan error, 1)
			var sandbox *Sandbox
			go func() {
				var err error
				sandbox, err = route.call(t.Context(), client, &CreateFromSnapshotOptions{Wait: WaitForever})
				result <- err
			}()
			<-arrived

			// When
			require.NoError(t, client.Close())
			err := <-result

			// Then: an SDK wait failure still returns the accepted handle.
			require.NoError(t, err)
			require.NotNil(t, sandbox)
			require.Equal(t, "sbx", sandbox.ID())
			require.Equal(t, SandboxStateCreating, sandbox.State())
		})
	}
}
