// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

type buildLogsRoundTrip func(*http.Request) (*http.Response, error)

func (f buildLogsRoundTrip) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

func TestBuildLogsStreamOutlivesRequestBudget(t *testing.T) {
	// Given a deterministic transport clock; a real network cannot run on
	// synctest's virtual clock. HTTP boundary behavior is covered separately.
	fake := newFakeServer(t, nil)
	synctest.Test(t, func(t *testing.T) {
		roundTrip := buildLogsRoundTrip(func(request *http.Request) (*http.Response, error) {
			if strings.HasSuffix(request.URL.Path, "build-logs-url") {
				return &http.Response{
					StatusCode: 200, Header: http.Header{},
					Body: io.NopCloser(strings.NewReader(`{"url":"http://logs.invalid/build-logs/images/image"}`)),
				}, nil
			}
			reader, writer := io.Pipe()
			go func() {
				timer := time.NewTimer(2 * time.Second)
				defer timer.Stop()
				select {
				case <-request.Context().Done():
				case <-timer.C:
					if _, err := io.WriteString(writer, buildLogsDone("empty")); err != nil {
						t.Errorf("write stream: %v", err)
					}
				}
				if err := writer.Close(); err != nil {
					t.Errorf("close stream: %v", err)
				}
			}()
			return &http.Response{
				StatusCode: 200, Header: http.Header{"Content-Type": {"text/event-stream"}},
				Body: reader,
			}, nil
		})
		client := newTestClient(t, fake.Server, WithRequestTimeout(time.Second),
			WithHTTPClient(&http.Client{Transport: roundTrip}))
		// When a healthy stream's first event arrives after the request timeout.
		events, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image", nil))
		// Then only the headers were timeout-bounded.
		require.NoError(t, err)
		require.Len(t, events, 1)
		require.True(t, buildLogsAs[BuildLogDoneEvent](t, events[0]).Result.OutputComplete)
	})
}

func TestBuildLogsObservationTimeoutStopsBody(t *testing.T) {
	// Given a quiet stream on a virtual clock.
	fake := newFakeServer(t, nil)
	synctest.Test(t, func(t *testing.T) {
		roundTrip := buildLogsRoundTrip(func(request *http.Request) (*http.Response, error) {
			if strings.HasSuffix(request.URL.Path, "build-logs-url") {
				return &http.Response{
					StatusCode: 200, Header: http.Header{},
					Body: io.NopCloser(strings.NewReader(`{"url":"http://logs.invalid/build-logs/images/image"}`)),
				}, nil
			}
			reader, writer := io.Pipe()
			go func() {
				<-request.Context().Done()
				if err := writer.CloseWithError(request.Context().Err()); err != nil {
					t.Errorf("close stream: %v", err)
				}
			}()
			return &http.Response{
				StatusCode: 200, Header: http.Header{"Content-Type": {"text/event-stream"}},
				Body: reader,
			}, nil
		})
		client := newTestClient(t, fake.Server, WithRequestTimeout(NoTimeout),
			WithHTTPClient(&http.Client{Transport: roundTrip}))
		// When the observation budget expires despite unbounded request time.
		_, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image",
			&FollowBuildLogsOptions{Cursor: "resume", Timeout: time.Second}))
		// Then timeout identity and the starting checkpoint survive.
		var interrupted *BuildLogInterruptedError
		require.ErrorAs(t, err, &interrupted)
		require.ErrorIs(t, err, context.DeadlineExceeded)
		require.Equal(t, CodeRequestTimeout, interrupted.SDKError.Code)
		require.Equal(t, "resume", interrupted.Cursor)
	})
}

func TestBuildLogsSnapshotBudgetIncludesBody(t *testing.T) {
	// Given a snapshot whose body never completes.
	fake := newFakeServer(t, nil)
	synctest.Test(t, func(t *testing.T) {
		roundTrip := buildLogsRoundTrip(func(request *http.Request) (*http.Response, error) {
			if strings.HasSuffix(request.URL.Path, "build-logs-url") {
				return &http.Response{
					StatusCode: 200, Header: http.Header{},
					Body: io.NopCloser(strings.NewReader(`{"url":"http://logs.invalid/build-logs/sandboxes/sbx"}`)),
				}, nil
			}
			reader, writer := io.Pipe()
			go func() {
				<-request.Context().Done()
				if err := writer.CloseWithError(request.Context().Err()); err != nil {
					t.Errorf("close snapshot: %v", err)
				}
			}()
			return &http.Response{StatusCode: 200, Header: http.Header{}, Body: reader}, nil
		})
		client := newTestClient(t, fake.Server, WithRequestTimeout(time.Second),
			WithHTTPClient(&http.Client{Transport: roundTrip}))
		// When reading one-shot output.
		_, err := (&Sandbox{client: client, id: "sbx"}).BuildLogs(t.Context())
		// Then the ordinary request budget bounds the full read.
		require.ErrorIs(t, err, ErrClientTimeout)
		require.ErrorIs(t, err, context.DeadlineExceeded)
	})
}
