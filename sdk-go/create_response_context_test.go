// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

type createCancelBody struct {
	io.ReadCloser
	cancel context.CancelFunc
}

func (b createCancelBody) Close() error {
	err := b.ReadCloser.Close()
	b.cancel()
	return err
}

func TestCreate_whenContextEndsAfterServerRefusal_preservesServerError(t *testing.T) {
	for _, route := range createTestCases() {
		for _, status := range []int{http.StatusBadRequest, http.StatusRequestTimeout, http.StatusServiceUnavailable} {
			t.Run(route.name+"/"+http.StatusText(status), func(t *testing.T) {
				// Given: cancellation during response cleanup, after the refusal was read.
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST " + route.path: func(w http.ResponseWriter, _ *http.Request) {
						w.Header().Set("Content-Type", "application/json")
						w.WriteHeader(status)
						writeJSON(t, w, map[string]string{"source": SourceAPI, "message": "create refused"})
					},
				})
				ctx, cancel := context.WithCancel(t.Context())
				defer cancel()
				tr := waitRetryTransport(func(r *http.Request) (*http.Response, error) {
					response, err := http.DefaultTransport.RoundTrip(r)
					if err != nil {
						return nil, err
					}
					response.Body = createCancelBody{ReadCloser: response.Body, cancel: cancel}
					return response, nil
				})
				client := newTestClient(t, fake.Server, WithHTTPClient(&http.Client{Transport: tr}))

				// When
				sandbox, err := route.call(ctx, client, &CreateFromSnapshotOptions{Wait: NoWait})

				// Then: the refusal is definitive, not an uncertain create outcome.
				require.Nil(t, sandbox)
				require.ErrorIs(t, ctx.Err(), context.Canceled)
				var detail *Error
				require.ErrorAs(t, err, &detail)
				require.Equal(t, status, detail.StatusCode)
				require.Equal(t, SourceAPI, detail.Source)
				require.Equal(t, "create refused", detail.Message)
				var connection *SandboxCreateConnectionError
				var timeout *SandboxCreateRequestTimeoutError
				require.NotErrorAs(t, err, &connection)
				require.NotErrorAs(t, err, &timeout)
			})
		}
	}
}
