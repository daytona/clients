// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/stretchr/testify/require"
)

func TestStreamProcess_retainsCreatedIdentity_whenCreationRacesCancellation(t *testing.T) {
	for _, terminal := range []bool{false, true} {
		t.Run(map[bool]string{false: "exec", true: "terminal"}[terminal], func(t *testing.T) {
			// Given
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/processes" {
					w.WriteHeader(http.StatusNotFound)
					return
				}
				cancel()
				_, _ = io.WriteString(w, `{"id":"created-before-cancel","state":"running"}`)
			}))
			defer srv.Close()
			client := newToolboxClient(srv)
			// When
			var result ProcessResult
			var err error
			if terminal {
				result, err = client.StreamTerminal(ctx, tbgen.CreateProcessRequest{})
			} else {
				result, err = client.StreamProcess(ctx, StreamProcessOptions{})
			}
			// Then
			require.Error(t, err)
			require.Equal(t, "created-before-cancel", result.Process.Id)
		})
	}
}

func TestStreamProcess_doesNotCreate_whenAlreadyCanceled(t *testing.T) {
	for _, terminal := range []bool{false, true} {
		t.Run(map[bool]string{false: "exec", true: "terminal"}[terminal], func(t *testing.T) {
			// Given
			var requests atomic.Int32
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests.Add(1)
			}))
			defer srv.Close()
			client := newToolboxClient(srv)
			ctx, cancel := context.WithCancel(t.Context())
			cancel()
			// When
			var err error
			if terminal {
				_, err = client.StreamTerminal(ctx, tbgen.CreateProcessRequest{})
			} else {
				_, err = client.StreamProcess(ctx, StreamProcessOptions{})
			}
			// Then
			require.Error(t, err)
			require.Zero(t, requests.Load())
		})
	}
}

type stalledReviewReader struct {
	started chan struct{}
	release chan struct{}
}

func (r *stalledReviewReader) Read([]byte) (int, error) {
	close(r.started)
	<-r.release
	return 0, io.EOF
}

func (r *stalledReviewReader) Close() error { return nil }

func TestStreamProcess_returnsStreamFailure_whenStdinCloseDoesNotUnblockRead(t *testing.T) {
	// Given
	reader := &stalledReviewReader{started: make(chan struct{}), release: make(chan struct{})}
	defer close(reader.release)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/processes":
			_, _ = io.WriteString(w, `{"id":"proc","state":"running"}`)
		case "/processes/proc/logs":
			<-reader.started
			w.WriteHeader(http.StatusBadGateway)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()
	client := newToolboxClient(srv)
	done := make(chan error, 1)
	// When
	go func() {
		_, err := client.StreamProcess(t.Context(), StreamProcessOptions{Stdin: reader})
		done <- err
	}()
	// Then
	select {
	case err := <-done:
		require.Error(t, err)
	case <-time.After(2 * time.Second):
		t.Fatal("stream failure blocked on an uninterruptible stdin reader")
	}
}
