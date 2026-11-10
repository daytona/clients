// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package workload

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestResourceWait_preservesAPIError_whenRequestTimesOutBeforeWaitBudget(t *testing.T) {
	for _, resource := range []string{"image", "snapshot"} {
		for _, status := range []int{http.StatusRequestTimeout, http.StatusGatewayTimeout} {
			t.Run(resource+"/"+http.StatusText(status), func(t *testing.T) {
				// Given
				srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(status)
					_, _ = w.Write([]byte(`{"code":"REQUEST_TIMEOUT","source":"DAYTONA_API","message":"upstream timed out"}`))
				}))
				defer srv.Close()
				client := api.New(srv.URL, "fixture-key", "test")

				// When
				var err error
				if resource == "image" {
					_, err = WaitForImage(context.Background(), client, "pending-resource", time.Minute)
				} else {
					_, err = WaitForSnapshot(context.Background(), client, "pending-resource", time.Minute)
				}

				// Then: the original API error is not relabeled as a local wait expiry.
				require.Error(t, err)
				require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
				var requestErr *api.APIError
				require.ErrorAs(t, err, &requestErr)
				require.Equal(t, status, requestErr.Status)
				require.NotContains(t, err.Error(), "daytona "+resource+" get")
			})
		}
	}
}

type reviewTimeoutTransport struct{}

func (reviewTimeoutTransport) RoundTrip(*http.Request) (*http.Response, error) {
	return nil, context.DeadlineExceeded
}

type reviewWaitBudgetTransport struct{}

func (reviewWaitBudgetTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	<-req.Context().Done()
	return nil, req.Context().Err()
}

func TestResourceWait_preservesTransportTimeout_whenWaitBudgetRemains(t *testing.T) {
	for _, resource := range []string{"image", "snapshot"} {
		t.Run(resource, func(t *testing.T) {
			// Given
			client := api.New("https://api.example", "fixture-key", "test")
			client.HTTP.Transport = reviewTimeoutTransport{}

			// When
			var err error
			if resource == "image" {
				_, err = WaitForImage(context.Background(), client, "pending-resource", time.Minute)
			} else {
				_, err = WaitForSnapshot(context.Background(), client, "pending-resource", time.Minute)
			}

			// Then
			require.Error(t, err)
			require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
			require.True(t, errors.Is(err, context.DeadlineExceeded))
			require.NotContains(t, err.Error(), "daytona "+resource+" get")
		})
	}
}

func TestResourceWait_namesPendingResource_whenLocalWaitBudgetExpires(t *testing.T) {
	for _, resource := range []string{"image", "snapshot"} {
		t.Run(resource, func(t *testing.T) {
			// Given: an expired budget cannot make any request.
			client := api.New("https://api.example", "fixture-key", "test")
			client.HTTP.Transport = reviewTimeoutTransport{}

			// When
			var err error
			if resource == "image" {
				_, err = WaitForImage(context.Background(), client, "pending-resource", 0)
			} else {
				_, err = WaitForSnapshot(context.Background(), client, "pending-resource", 0)
			}

			// Then
			require.Error(t, err)
			require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
			require.ErrorContains(t, err, "daytona "+resource+" get pending-resource")
		})
	}
}

func TestResourceWait_namesPendingResource_whenLocalBudgetExpiresDuringRequest(t *testing.T) {
	for _, resource := range []string{"image", "snapshot"} {
		t.Run(resource, func(t *testing.T) {
			// Given: the request stays pending until its local wait deadline.
			client := api.New("https://api.example", "fixture-key", "test")
			client.HTTP.Transport = reviewWaitBudgetTransport{}

			// When
			var err error
			if resource == "image" {
				_, err = WaitForImage(context.Background(), client, "pending-resource", time.Millisecond)
			} else {
				_, err = WaitForSnapshot(context.Background(), client, "pending-resource", time.Millisecond)
			}

			// Then
			require.Error(t, err)
			require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
			require.ErrorContains(t, err, "daytona "+resource+" get pending-resource")
		})
	}
}
