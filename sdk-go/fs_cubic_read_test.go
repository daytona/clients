// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"io"
	"net/http"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

type fsCubicResponseTransport struct {
	next   http.RoundTripper
	change func(*http.Response)
}

func (t fsCubicResponseTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	response, err := t.next.RoundTrip(request)
	if err == nil {
		t.change(response)
	}
	return response, err
}

func TestFileBufferedRead_whenDeclaredLengthExceedsBody_rejectsPartialData(t *testing.T) {
	for _, bulk := range []bool{false, true} {
		t.Run(map[bool]string{false: "ReadFile", true: "ReadMany"}[bulk], func(t *testing.T) {
			// Given a complete HTTP body with an overstated length from a custom transport.
			body := "payload"
			route := "GET /files/download"
			if bulk {
				body = "--DAYTONA-FILE-BOUNDARY--\r\n"
				route = "POST /files/bulk-download"
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{route: func(w http.ResponseWriter, _ *http.Request) {
				if _, err := io.WriteString(w, body); err != nil {
					t.Error(err)
				}
			}})
			client := fake.Server.Client()
			client.Transport = fsCubicResponseTransport{
				next: client.Transport,
				change: func(response *http.Response) {
					response.ContentLength++
					response.Header.Set("Content-Length", strconv.FormatInt(response.ContentLength, 10))
				},
			}
			fs := newFileTestService(t, fake.Server, WithHTTPClient(client))

			// When buffering either kind of file response.
			var err error
			if bulk {
				_, err = fs.ReadMany(t.Context(), []string{"/a"}, nil)
			} else {
				_, err = fs.ReadFile(t.Context(), "/a", nil)
			}

			// Then a clean EOF cannot turn a truncated response into success.
			require.ErrorIs(t, err, ErrConnection)
			require.ErrorIs(t, err, io.ErrUnexpectedEOF)
		})
	}
}
