// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestContextUpload_whenMoreThanFourParts(t *testing.T) {
	// Given storage holding its first four requests until all four arrive.
	var active, peak, arrived atomic.Int64
	full := make(chan struct{})
	server := contextStorageServer(t, func(w http.ResponseWriter, r *http.Request) {
		contextDrainRequest(t, r)
		now := active.Add(1)
		defer active.Add(-1)
		for maximum := peak.Load(); now > maximum; maximum = peak.Load() {
			if peak.CompareAndSwap(maximum, now) {
				break
			}
		}
		if arrived.Add(1) == 4 {
			close(full)
		}
		select {
		case <-full:
			w.WriteHeader(http.StatusNoContent)
		case <-r.Context().Done():
		}
	})
	client := newTestClient(t, server)
	uploader := contextTestUploader(t, client, []byte("abcdefgh"))
	parts := make([]contextUploadPart, 8)
	for index := range parts {
		parts[index] = contextUploadPart{
			number: int64(index) + 1, bytes: 1, offset: int64(index),
			url: server.URL + contextPartPath(int64(index)+1),
		}
	}
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()

	// When every part is sent.
	err := uploader.putParts(ctx, parts)

	// Then uploads are parallel, bounded to four, and every part finishes.
	require.NoError(t, err)
	require.EqualValues(t, 4, peak.Load())
	require.EqualValues(t, 8, arrived.Load())
}

func TestContextUpload_whenOneParallelPartFails(t *testing.T) {
	// Given three blocked siblings and a fourth part that refuses the upload.
	var arrived atomic.Int64
	full := make(chan struct{})
	canceled := make(chan struct{}, 3)
	server := contextStorageServer(t, func(w http.ResponseWriter, r *http.Request) {
		contextDrainRequest(t, r)
		if arrived.Add(1) == 4 {
			close(full)
		}
		select {
		case <-full:
		case <-r.Context().Done():
			return
		}
		if r.URL.Path == "/part/1" {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		<-r.Context().Done()
		canceled <- struct{}{}
	})
	client := newTestClient(t, server)
	uploader := contextTestUploader(t, client, []byte("abcd"))
	parts := make([]contextUploadPart, 4)
	for index := range parts {
		parts[index] = contextUploadPart{
			number: int64(index) + 1, bytes: 1, offset: int64(index),
			url: server.URL + contextPartPath(int64(index)+1),
		}
	}
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()

	// When the refusing part fails.
	err := uploader.putParts(ctx, parts)

	// Then its original error wins, all siblings cancel, and every worker is joined.
	require.ErrorIs(t, err, ErrBadRequest)
	for range 3 {
		select {
		case <-canceled:
		case <-ctx.Done():
			t.Fatal("storage sibling was not canceled")
		}
	}
}

func TestContextUpload_whenCallerCancelsStorage(t *testing.T) {
	// Given one storage request waiting for its response headers.
	started := make(chan struct{})
	stopped := make(chan struct{})
	server := contextStorageServer(t, func(_ http.ResponseWriter, r *http.Request) {
		contextDrainRequest(t, r)
		close(started)
		<-r.Context().Done()
		close(stopped)
	})
	client := newTestClient(t, server, WithRequestTimeout(NoTimeout))
	uploader := contextTestUploader(t, client, []byte("abc"))
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	go func() {
		select {
		case <-started:
			cancel()
		case <-ctx.Done():
		}
	}()

	// When the caller cancels while storage is blocked.
	err := uploader.putParts(ctx, []contextUploadPart{{number: 1, bytes: 3, url: server.URL + "/part"}})

	// Then the request and worker terminate and the caller can match cancellation.
	require.ErrorIs(t, err, context.Canceled)
	select {
	case <-stopped:
	case <-time.After(5 * time.Second):
		t.Fatal("storage request was not canceled")
	}
}

func TestContextUpload_whenClientClosesStorage(t *testing.T) {
	// Given a client closing while its storage upload is active.
	started := make(chan struct{})
	server := contextStorageServer(t, func(_ http.ResponseWriter, r *http.Request) {
		contextDrainRequest(t, r)
		close(started)
		<-r.Context().Done()
	})
	client := newTestClient(t, server, WithRequestTimeout(NoTimeout))
	uploader := contextTestUploader(t, client, []byte("abc"))
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()
	var shutdown sync.WaitGroup
	shutdown.Go(func() {
		select {
		case <-started:
		case <-ctx.Done():
			return
		}
		if err := client.Close(); err != nil {
			t.Error(err)
		}
	})

	// When the client closes.
	err := uploader.putParts(ctx, []contextUploadPart{{number: 1, bytes: 3, url: server.URL + "/part"}})
	shutdown.Wait()

	// Then direct storage requests also observe Client.Close.
	require.ErrorIs(t, err, ErrClientClosed)
}
