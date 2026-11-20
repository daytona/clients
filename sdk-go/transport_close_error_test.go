// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"syscall"
	"testing"

	"github.com/stretchr/testify/require"
)

type responseCloseFailure struct{ io.Reader }

func (responseCloseFailure) Close() error { return syscall.EIO }

func TestTransport_whenResponseCloseFails_preservesCloseCause(t *testing.T) {
	for _, streaming := range []bool{false, true} {
		t.Run(map[bool]string{false: "buffered", true: "stream"}[streaming], func(t *testing.T) {
			// Given: EOF or scope cleanup may cancel the request before Close.
			transport := buildLogsRoundTrip(func(*http.Request) (*http.Response, error) {
				return &http.Response{
					StatusCode: http.StatusOK, Header: make(http.Header),
					Body: responseCloseFailure{strings.NewReader("")},
				}, nil
			})
			client := newTestClient(t, &httptest.Server{URL: "http://close.example"},
				WithHTTPClient(&http.Client{Transport: transport}))

			// When: the buffered call or stream owner closes the response body.
			var err error
			if streaming {
				var response *http.Response
				response, err = client.api().stream(t.Context(), apiRequest{Path: "/"})
				require.NoError(t, err)
				err = response.Body.Close()
			} else {
				err = client.api().do(t.Context(), apiRequest{Path: "/"})
			}

			// Then
			require.ErrorIs(t, err, syscall.EIO)
			require.NotErrorIs(t, err, context.Canceled)
			require.ErrorContains(t, err, syscall.EIO.Error())
		})
	}
}
