// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

// A wire-level adapter lets synctest observe blocking requests as channels,
// without external network activity preventing deterministic scheduler control.
type interpreterQueueTransport struct {
	started chan struct{}
	release chan struct{}
	posts   atomic.Int64
}

func (f *interpreterQueueTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	var body string
	switch {
	case r.Method == http.MethodPost:
		index := f.posts.Add(1)
		raw, err := json.Marshal(interpreterProcessFixture(fmt.Sprintf("p%d", index)))
		if err != nil {
			return nil, err
		}
		body = string(raw)
	case r.Method == http.MethodDelete:
		body = "{}"
	case strings.HasSuffix(r.URL.Path, "/wait"):
		if strings.Contains(r.URL.Path, "/p1/") {
			close(f.started)
			select {
			case <-f.release:
			case <-r.Context().Done():
				return nil, r.Context().Err()
			}
		}
		body = `{"reason":"exited","exitCode":0}`
	case strings.HasSuffix(r.URL.Path, "/logs"):
		body = `{"frames":[],"nextCursor":"0","eof":true,"truncatedHead":false}`
	default:
		return nil, badRequest("unexpected queue-test request")
	}
	return &http.Response{
		StatusCode: http.StatusOK, Header: http.Header{"Content-Type": {"application/json"}},
		Body: io.NopCloser(strings.NewReader(body)), Request: r,
	}, nil
}

func interpreterQueueSandbox(t *testing.T, transport *interpreterQueueTransport) *Sandbox {
	t.Helper()
	client, err := New(WithAPIKey("queue-fixture"), WithHTTPClient(&http.Client{Transport: transport}))
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, client.Close()) })
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetSandboxOptions{ToolboxURL: "http://sandbox.invalid", AccessToken: "sandbox-fixture"},
	})
	require.NoError(t, err)
	return sandbox
}

func TestRunCode_whenConcurrentSameContext(t *testing.T) {
	for _, explicit := range []bool{false, true} {
		t.Run(fmt.Sprintf("explicit=%t", explicit), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given
				transport := &interpreterQueueTransport{started: make(chan struct{}), release: make(chan struct{})}
				sandbox := interpreterQueueSandbox(t, transport)
				options := RunCodeOptions{}
				if explicit {
					options.ContextID, options.Language = "ctx", "python"
				}
				first := make(chan error, 1)
				go func() {
					_, err := sandbox.RunCode(t.Context(), "first", &options)
					first <- err
				}()
				<-transport.started
				second := make(chan error, 1)
				// When
				go func() {
					_, err := sandbox.Interpreter().Run(t.Context(), "second", &options)
					second <- err
				}()
				synctest.Wait()
				// Then
				require.EqualValues(t, 1, transport.posts.Load())
				close(transport.release)
				require.NoError(t, <-first)
				require.NoError(t, <-second)
				require.EqualValues(t, 2, transport.posts.Load())
			})
		})
	}
}

func TestRunCode_whenQueuedCallerCancels(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		transport := &interpreterQueueTransport{started: make(chan struct{}), release: make(chan struct{})}
		sandbox := interpreterQueueSandbox(t, transport)
		first := make(chan error, 1)
		go func() {
			_, err := sandbox.RunCode(t.Context(), "first", nil)
			first <- err
		}()
		<-transport.started
		ctx, cancel := context.WithCancel(t.Context())
		defer cancel()
		second := make(chan error, 1)
		go func() {
			_, err := sandbox.RunCode(ctx, "second", nil)
			second <- err
		}()
		synctest.Wait()
		// When
		cancel()
		// Then
		require.ErrorIs(t, <-second, context.Canceled)
		require.EqualValues(t, 1, transport.posts.Load())
		close(transport.release)
		require.NoError(t, <-first)
	})
}

func TestRunCode_whenQueuedBudgetExpires(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		transport := &interpreterQueueTransport{started: make(chan struct{}), release: make(chan struct{})}
		sandbox := interpreterQueueSandbox(t, transport)
		first := make(chan error, 1)
		go func() {
			_, err := sandbox.RunCode(t.Context(), "first", nil)
			first <- err
		}()
		<-transport.started
		// When — synctest advances its virtual clock while the queue is blocked.
		result, err := sandbox.RunCode(t.Context(), "second", &RunCodeOptions{Timeout: time.Second})
		// Then
		require.NoError(t, err)
		require.Equal(t, ProcessReasonTimedOut, result.Process.Reason)
		require.Empty(t, result.Process.ProcessID)
		require.EqualValues(t, 1, transport.posts.Load())
		close(transport.release)
		require.NoError(t, <-first)
	})
}

func TestRunCode_whenContextDeletedWhileQueued(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		transport := &interpreterQueueTransport{started: make(chan struct{}), release: make(chan struct{})}
		sandbox := interpreterQueueSandbox(t, transport)
		bound, err := sandbox.Interpreter().ConnectContext("ctx", "python")
		require.NoError(t, err)
		first, second := make(chan error, 1), make(chan error, 1)
		go func() {
			_, runErr := bound.Run(t.Context(), "first", nil)
			first <- runErr
		}()
		<-transport.started
		go func() {
			_, runErr := bound.Run(t.Context(), "second", nil)
			second <- runErr
		}()
		synctest.Wait()
		// When
		err = bound.Close(t.Context())
		// Then
		require.NoError(t, err)
		require.EqualValues(t, 1, transport.posts.Load())
		require.Len(t, sandbox.Interpreter().queues, 1)
		close(transport.release)
		require.NoError(t, <-first)
		require.NoError(t, <-second)
		require.Empty(t, sandbox.Interpreter().queues)
	})
}
