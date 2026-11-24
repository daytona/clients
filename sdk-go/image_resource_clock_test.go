// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

// Keep socket goroutines outside synctest; inside it, run the same core wire
// fake through an HTTP transport so its timers and request contexts share time.
func imageClockClient(t *testing.T, fake *fakeServer, timeout time.Duration) *Client {
	t.Helper()
	return newTestClient(t, fake.Server, WithRequestTimeout(timeout), WithHTTPClient(&http.Client{
		Transport: imageHandlerTransport{handler: fake.Server.Config.Handler},
	}))
}

type imageHandlerTransport struct{ handler http.Handler }

func (transport imageHandlerTransport) RoundTrip(input *http.Request) (*http.Response, error) {
	request := input.Clone(input.Context())
	if request.Body == nil {
		request.Body = http.NoBody
	}
	recorder := httptest.NewRecorder()
	transport.handler.ServeHTTP(recorder, request)
	if err := request.Body.Close(); err != nil {
		return nil, err
	}
	if err := request.Context().Err(); err != nil {
		return nil, err
	}
	return recorder.Result(), nil
}

func imageDelay(ctx context.Context, duration time.Duration) error {
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-timer.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func imageArtifactResources() []struct {
	name    string
	fixture func(string, string) map[string]any
	get     func(context.Context, *Client, *GetArtifactOptions) (string, error)
} {
	return []struct {
		name    string
		fixture func(string, string) map[string]any
		get     func(context.Context, *Client, *GetArtifactOptions) (string, error)
	}{
		{"images", imageFixture, func(ctx context.Context, client *Client, opts *GetArtifactOptions) (string, error) {
			image, err := client.Images().Get(ctx, "artifact/one", opts)
			if err != nil {
				return "", err
			}
			return image.RawStatus, nil
		}},
		{"snapshots", snapshotFixture, func(ctx context.Context, client *Client, opts *GetArtifactOptions) (string, error) {
			snapshot, err := client.Snapshots().Get(ctx, "artifact/one", opts)
			if err != nil {
				return "", err
			}
			return snapshot.RawStatus, nil
		}},
	}
}

func requireImageTimeout(t *testing.T, err error) {
	t.Helper()
	require.ErrorIs(t, err, ErrRequestTimeout)
	require.NotErrorIs(t, err, ErrClientTimeout)
	require.ErrorIs(t, err, context.DeadlineExceeded)
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.Equal(t, SourceSDK, detail.Source)
	require.Equal(t, 408, detail.StatusCode)
}
