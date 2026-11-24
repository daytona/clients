// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestContextUpload_whenStorageRequiresRetry(t *testing.T) {
	for _, test := range []struct {
		name     string
		statuses []int
		want     error
	}{
		{"transient", []int{503, 200}, nil},
		{"timeout_and_throttle", []int{408, 429, 204}, nil},
		{"persistent", []int{500, 502, 503}, ErrServiceUnavailable},
		{"refused", []int{403}, ErrBadRequest},
		{"redirect", []int{302}, nil},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given storage with a known sequence of failures.
			var calls atomic.Int64
			server := contextStorageServer(t, func(w http.ResponseWriter, r *http.Request) {
				body, err := io.ReadAll(r.Body)
				assert.NoError(t, err)
				assert.Equal(t, []byte("abc"), body)
				call := int(calls.Add(1)) - 1
				assert.Less(t, call, len(test.statuses))
				w.Header().Set("Location", "/must-not-follow")
				w.WriteHeader(test.statuses[min(call, len(test.statuses)-1)])
			})
			client := newTestClient(t, server)
			uploader := contextTestUploader(t, client, []byte("abc"))
			part := contextUploadPart{number: 1, bytes: 3, url: server.URL + "/part?signature=secret"}

			// When a part is uploaded.
			err := uploader.putPart(t.Context(), part)

			// Then retryability and the three-attempt cap match the storage contract.
			require.EqualValues(t, len(test.statuses), calls.Load())
			switch {
			case test.want != nil:
				require.ErrorIs(t, err, test.want)
			case test.name == "redirect":
				var sdkError *Error
				require.ErrorAs(t, err, &sdkError)
				require.Equal(t, 302, sdkError.StatusCode)
				require.Equal(t, "SDK", sdkError.Source)
			default:
				require.NoError(t, err)
			}
			if err != nil {
				require.NotContains(t, fmt.Sprintf("%#v", err), "signature")
				require.NoError(t, errors.Unwrap(err))
			}
		})
	}
}

func TestContextUpload_whenNetworkErrorsContainCredentials(t *testing.T) {
	// Given a transport whose failure includes the complete signed URL.
	var attempts atomic.Int64
	server := contextStorageServer(t, http.NotFound)
	transport := contextTestTransport(func(request *http.Request) (*http.Response, error) {
		attempts.Add(1)
		if err := request.Body.Close(); err != nil {
			return nil, err
		}
		return nil, fmt.Errorf("connect %s: refused", request.URL)
	})
	client := newTestClient(t, server, WithHTTPClient(&http.Client{Transport: transport}))
	uploader := contextTestUploader(t, client, []byte("abc"))

	// When all three attempts fail.
	err := uploader.putPart(t.Context(), contextUploadPart{
		number: 1, bytes: 3, url: server.URL + "/private-path?signature=secret",
	})

	// Then the connection error preserves its category, but no credential-bearing cause.
	require.ErrorIs(t, err, ErrConnection)
	require.EqualValues(t, 3, attempts.Load())
	require.NoError(t, errors.Unwrap(err))
	require.NotContains(t, fmt.Sprintf("%#v", err), "private-path")
	require.NotContains(t, fmt.Sprintf("%#v", err), "secret")
}

func TestContextUpload_whenArchiveChangesBeforePart(t *testing.T) {
	// Given a file truncated after its upload metadata was prepared.
	var calls atomic.Int64
	server := contextStorageServer(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		contextDrainRequest(t, r)
		w.WriteHeader(http.StatusNoContent)
	})
	client := newTestClient(t, server)
	uploader := contextTestUploader(t, client, []byte("abc"))
	require.NoError(t, os.Truncate(uploader.archive.Path, 1))

	// When its part is uploaded.
	err := uploader.putPart(t.Context(), contextUploadPart{number: 1, bytes: 3, url: server.URL + "/part"})

	// Then changed input is rejected without a retry or storage request.
	require.ErrorIs(t, err, ErrBadRequest)
	require.Zero(t, calls.Load())
}

func TestContextUpload_whenCompletionRequiresRetry(t *testing.T) {
	for _, test := range []struct {
		name     string
		statuses []int
		want     error
	}{
		{"transient", []int{503, 502, 200}, nil},
		{"persistent", []int{500, 500, 500}, ErrInternalServer},
		{"checksum_rejected", []int{409}, ErrConflict},
		{"not_implemented", []int{501}, ErrNotSupported},
		{"server_timeout", []int{408}, ErrTimeout},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given verification that fails with the selected API status sequence.
			var calls atomic.Int64
			server := contextStorageServer(t, func(w http.ResponseWriter, r *http.Request) {
				contextDrainRequest(t, r)
				call := int(calls.Add(1)) - 1
				status := test.statuses[min(call, len(test.statuses)-1)]
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(status)
				writeJSON(t, w, map[string]string{"message": "verification failed", "source": "API"})
			})
			client := newTestClient(t, server)
			uploader := contextTestUploader(t, client, []byte("abc"))

			// When completion is attempted.
			err := uploader.complete(t.Context(), buildContextUpload{ID: "ctx", Region: "region"})

			// Then only safe completion failures retry; checksum rejection stays a conflict.
			require.EqualValues(t, len(test.statuses), calls.Load())
			if test.want != nil {
				require.ErrorIs(t, err, test.want)
			} else {
				require.NoError(t, err)
			}
		})
	}
}

func TestContextUpload_whenRetryPauseIsCanceled(t *testing.T) {
	// Given a retry delay whose caller is already canceled.
	ctx, cancel := context.WithCancel(t.Context())
	cancel()

	// When the retry pause begins.
	err := contextUploadPause(ctx, defaultRequestTimeout)

	// Then cancellation is immediate and remains reachable.
	require.ErrorIs(t, err, context.Canceled)
}
