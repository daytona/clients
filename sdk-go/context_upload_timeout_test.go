// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestContextUpload_whenStorageStopsProgress(t *testing.T) {
	// Given storage accepting bytes but never returning response headers.
	var attempts atomic.Int64
	stopped := make(chan struct{}, 3)
	server := contextStorageServer(t, func(_ http.ResponseWriter, r *http.Request) {
		contextDrainRequest(t, r)
		attempts.Add(1)
		<-r.Context().Done()
		stopped <- struct{}{}
	})
	client := newTestClient(t, server, WithRequestTimeout(100*time.Millisecond))
	uploader := contextTestUploader(t, client, []byte("abc"))

	// When each attempt exhausts its idle budget.
	err := uploader.putPart(t.Context(), contextUploadPart{
		number: 1, bytes: 3, url: server.URL + "/private?signature=secret",
	})

	// Then the final failure is a URL-free client timeout and all requests close.
	require.ErrorIs(t, err, ErrClientTimeout)
	require.ErrorIs(t, err, context.DeadlineExceeded)
	require.EqualValues(t, 3, attempts.Load())
	require.NotContains(t, err.Error(), "signature")
	for range 3 {
		select {
		case <-stopped:
		case <-time.After(5 * time.Second):
			t.Fatal("timed-out storage request remained active")
		}
	}
}

func TestContextUpload_whenProgressOutlivesRequestTimeout(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a paced transport that consumes one piece every 400 ms.
		var largest, total int
		transport := contextTestTransport(func(r *http.Request) (*http.Response, error) {
			defer func() { require.NoError(t, r.Body.Close()) }()
			buffer := make([]byte, 128<<10)
			for {
				n, readErr := r.Body.Read(buffer)
				if n > 0 {
					largest = max(largest, n)
					total += n
					timer := time.NewTimer(400 * time.Millisecond)
					select {
					case <-timer.C:
					case <-r.Context().Done():
						timer.Stop()
						return nil, r.Context().Err()
					}
				}
				if errors.Is(readErr, io.EOF) {
					break
				}
				if readErr != nil {
					return nil, readErr
				}
			}
			return &http.Response{
				StatusCode: 204, Header: make(http.Header), Body: http.NoBody,
			}, nil
		})
		client, err := New(WithAPIKey("key"), WithRequestTimeout(time.Second),
			WithHTTPClient(&http.Client{Transport: transport}))
		require.NoError(t, err)
		defer func() { require.NoError(t, client.Close()) }()
		uploader := contextTestUploader(t, client, []byte(strings.Repeat("x", 4*(64<<10)+1)))
		start := time.Now()

		// When the part takes two seconds in total but keeps moving.
		err = uploader.putPart(t.Context(), contextUploadPart{
			number: 1, bytes: uploader.archive.Size, url: "https://storage.invalid/part",
		})

		// Then no total timeout cuts it and every progress piece is at most 64 KiB.
		require.NoError(t, err)
		require.Greater(t, time.Since(start), time.Second)
		require.EqualValues(t, uploader.archive.Size, total)
		require.LessOrEqual(t, largest, 64<<10)
	})
}

func TestContextUpload_whenCompletionOutlivesOrdinaryTimeout(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a completion taking two minutes, above the client's one-second default.
		transport := contextTestTransport(func(r *http.Request) (*http.Response, error) {
			defer func() { require.NoError(t, r.Body.Close()) }()
			timer := time.NewTimer(2 * time.Minute)
			defer timer.Stop()
			select {
			case <-r.Context().Done():
				return nil, r.Context().Err()
			case <-timer.C:
				return &http.Response{StatusCode: 204, Header: make(http.Header), Body: http.NoBody}, nil
			}
		})
		client, err := New(WithAPIKey("key"), WithRequestTimeout(time.Second),
			WithHTTPClient(&http.Client{Transport: transport}))
		require.NoError(t, err)
		defer func() { require.NoError(t, client.Close()) }()
		uploader := contextTestUploader(t, client, []byte("abc"))

		// When verification runs under its own ten-minute budget.
		err = uploader.complete(t.Context(), buildContextUpload{ID: "ctx", Region: "region"})

		// Then the ordinary request timeout does not interrupt archive verification.
		require.NoError(t, err)
	})
}

func TestContextUpload_whenCompletionTimesOutThenSucceeds(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given one completion that stalls and a successful retry.
		var calls int
		transport := contextTestTransport(func(r *http.Request) (*http.Response, error) {
			defer func() { require.NoError(t, r.Body.Close()) }()
			calls++
			if calls == 1 {
				<-r.Context().Done()
				return nil, r.Context().Err()
			}
			return &http.Response{StatusCode: 204, Header: make(http.Header), Body: http.NoBody}, nil
		})
		client, err := New(WithAPIKey("key"), WithRequestTimeout(time.Second),
			WithHTTPClient(&http.Client{Transport: transport}))
		require.NoError(t, err)
		defer func() { require.NoError(t, client.Close()) }()
		uploader := contextTestUploader(t, client, []byte("abc"))
		start := time.Now()

		// When completion is retried after its ten-minute timeout.
		err = uploader.complete(t.Context(), buildContextUpload{ID: "ctx", Region: "region"})

		// Then a request timeout is retryable and the identical upload completes.
		require.NoError(t, err)
		require.Equal(t, 2, calls)
		require.Equal(t, 10*time.Minute, time.Since(start))
	})
}

func TestContextUpload_whenTimeoutsAreDisabled(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a caller choosing NoTimeout and storage and completion each taking an hour.
		transport := contextTestTransport(func(r *http.Request) (*http.Response, error) {
			defer func() { require.NoError(t, r.Body.Close()) }()
			timer := time.NewTimer(time.Hour)
			defer timer.Stop()
			select {
			case <-r.Context().Done():
				return nil, r.Context().Err()
			case <-timer.C:
				return &http.Response{StatusCode: 204, Header: make(http.Header), Body: http.NoBody}, nil
			}
		})
		client, err := New(WithAPIKey("key"), WithRequestTimeout(NoTimeout),
			WithHTTPClient(&http.Client{Transport: transport}))
		require.NoError(t, err)
		defer func() { require.NoError(t, client.Close()) }()
		uploader := contextTestUploader(t, client, []byte("abc"))

		// When storage accepts the part and the API verifies it.
		err = uploader.putPart(t.Context(), contextUploadPart{
			number: 1, bytes: 3, url: "https://storage.invalid/part",
		})
		require.NoError(t, err)
		err = uploader.complete(t.Context(), buildContextUpload{ID: "ctx", Region: "region"})

		// Then neither phase installs an SDK deadline when the client disables it.
		require.NoError(t, err)
	})
}
