// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestBuildLogsCancellationRetainsDeliveredCheckpoint(t *testing.T) {
	for _, closeClient := range []bool{false, true} {
		name := "context"
		if closeClient {
			name = "client"
		}
		t.Run(name, func(t *testing.T) {
			// Given a healthy stream blocked after a delivered chunk.
			disconnected := make(chan struct{})
			fake := buildLogsFake(t, func(w http.ResponseWriter, r *http.Request) {
				buildLogsSSE(t, buildLogsChunk("a", 0))(w, r)
				if err := http.NewResponseController(w).Flush(); err != nil {
					t.Errorf("flush stream: %v", err)
					return
				}
				<-r.Context().Done()
				close(disconnected)
			})
			client := newTestClient(t, fake.Server)
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			var failure error
			// When observation is interrupted after delivery.
			for _, err := range (&Images{client: client}).FollowBuildLogs(ctx, "image", nil) {
				if err != nil {
					failure = err
					break
				}
				if closeClient {
					require.NoError(t, client.Close())
				} else {
					cancel()
				}
			}
			// Then the checkpoint is preserved and the response is closed.
			var interrupted *BuildLogInterruptedError
			require.ErrorAs(t, failure, &interrupted)
			require.Equal(t, "checkpoint/1+=", interrupted.Cursor)
			if closeClient {
				require.ErrorIs(t, failure, ErrClientClosed)
			} else {
				require.ErrorIs(t, failure, context.Canceled)
				require.Equal(t, "BUILD_LOG_CANCELED", interrupted.SDKError.Code)
			}
			select {
			case <-disconnected:
			case <-time.After(2 * time.Second):
				t.Fatal("stream did not close")
			}
			require.Len(t, fake.requests(), 2)
		})
	}
}

func TestBuildLogsBreakClosesBody(t *testing.T) {
	// Given an open stream.
	disconnected := make(chan struct{})
	fake := buildLogsFake(t, func(w http.ResponseWriter, r *http.Request) {
		buildLogsSSE(t, buildLogsChunk("a", 0))(w, r)
		if err := http.NewResponseController(w).Flush(); err != nil {
			t.Errorf("flush stream: %v", err)
			return
		}
		<-r.Context().Done()
		close(disconnected)
	})
	client := newTestClient(t, fake.Server)
	// When the consumer breaks.
	for event, err := range (&Images{client: client}).FollowBuildLogs(t.Context(), "image", nil) {
		require.NoError(t, err)
		require.IsType(t, BuildLogChunkEvent{}, event)
		break
	}
	// Then the server sees closure without cancellation of the owning client.
	select {
	case <-disconnected:
	case <-time.After(2 * time.Second):
		t.Fatal("stream did not close")
	}
	require.NoError(t, client.ensureOpen())
	require.Len(t, fake.requests(), 2)
}

func TestBuildLogsNoTimeoutAndWaitForever(t *testing.T) {
	for _, timeout := range []time.Duration{0, NoTimeout, WaitForever, maxTimerDuration + 1} {
		t.Run(timeout.String(), func(t *testing.T) {
			// Given both request and observation timeout sentinels.
			fake := buildLogsFake(t, buildLogsSSE(t, buildLogsDone("empty")))
			client := newTestClient(t, fake.Server, WithRequestTimeout(NoTimeout))
			// When observing with an unbounded budget.
			events, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image",
				&FollowBuildLogsOptions{Timeout: timeout}))
			// Then a negative sentinel never becomes an expired deadline.
			require.NoError(t, err)
			require.Len(t, events, 1)
		})
	}
}

func TestBuildLogsExpiredContextDoesNotDiscover(t *testing.T) {
	// Given an already expired caller deadline.
	fake := buildLogsFake(t, buildLogsSSE(t, ""))
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithDeadline(t.Context(), time.Unix(1, 0))
	defer cancel()
	// When following from a checkpoint.
	_, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(ctx, "image",
		&FollowBuildLogsOptions{Cursor: "resume"}))
	// Then deadline identity survives the typed interruption.
	var interrupted *BuildLogInterruptedError
	require.ErrorAs(t, err, &interrupted)
	require.ErrorIs(t, err, context.DeadlineExceeded)
	require.Equal(t, CodeRequestTimeout, interrupted.SDKError.Code)
	require.Equal(t, "resume", interrupted.Cursor)
	require.Empty(t, fake.requests())
}

func TestBuildLogsCancelUnblocksIdleRead(t *testing.T) {
	// Given a stream with headers but no events.
	opened := make(chan struct{})
	disconnected := make(chan struct{})
	fake := buildLogsFake(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		if err := http.NewResponseController(w).Flush(); err != nil {
			t.Errorf("flush headers: %v", err)
			return
		}
		close(opened)
		<-r.Context().Done()
		close(disconnected)
	})
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	result := make(chan error, 1)
	go func() {
		_, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(ctx, "image", nil))
		result <- err
	}()
	<-opened
	// When the caller cancels while the body read is blocked.
	cancel()
	// Then reading exits without waiting for another server byte.
	select {
	case err := <-result:
		require.ErrorIs(t, err, context.Canceled)
		var interrupted *BuildLogInterruptedError
		require.ErrorAs(t, err, &interrupted)
	case <-time.After(2 * time.Second):
		t.Fatal("idle read did not unblock")
	}
	<-disconnected
}
