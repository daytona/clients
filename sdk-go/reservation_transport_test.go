// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReservationsCancellationInFlight(t *testing.T) {
	// Given
	entered := make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /reservations/id": func(_ http.ResponseWriter, r *http.Request) {
			close(entered)
			<-r.Context().Done()
		},
	})
	service := newTestClient(t, fake.Server).Reservations()
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	result := make(chan error, 1)
	// When
	go func() {
		_, err := service.Get(ctx, "id")
		result <- err
	}()
	<-entered
	cancel()
	// Then
	require.ErrorIs(t, <-result, context.Canceled)
	require.Len(t, fake.requests(), 1)
}

func TestReservationsClosedClient(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	service := client.Reservations()
	require.NoError(t, client.Close())
	// When
	_, err := service.Get(t.Context(), "id")
	// Then
	require.ErrorIs(t, err, ErrClientClosed)
	require.Empty(t, fake.requests())
}

func TestReservationsBulkBoundaryCounts(t *testing.T) {
	for _, count := range []int{1, 100} {
		for _, minCount := range []int{0, 1, count} {
			t.Run("counts", func(t *testing.T) {
				// Given
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST /reservations/bulk": jsonHandler(t, map[string]any{
						"requested": count, "reserved": count, "reservations": []any{},
					}),
				})
				service := newTestClient(t, fake.Server).Reservations()
				autoRenew := true
				// When
				batch, err := service.CreateBulk(t.Context(), "region", count, &CreateReservationsBulkOptions{
					MinCount: minCount, AutoRenew: &autoRenew,
				})
				// Then
				require.NoError(t, err)
				require.EqualValues(t, count, batch.Requested)
				require.EqualValues(t, count, batch.Reserved)
				requests := fake.requests()
				require.Len(t, requests, 1)
				body, parseErr := parseObject(requests[0].Body, "request")
				require.NoError(t, parseErr)
				require.JSONEq(t, "true", string(body["auto_renew"]))
				wireCount, parseErr := integerField(body, "count", true)
				require.NoError(t, parseErr)
				require.EqualValues(t, count, wireCount)
				if minCount == 0 {
					require.NotContains(t, body, "min_count")
				} else {
					wireMinCount, minCountErr := integerField(body, "min_count", true)
					require.NoError(t, minCountErr)
					require.EqualValues(t, minCount, wireMinCount)
				}
			})
		}
	}
}

func TestReservationsPreservesLimitAndNotFoundErrors(t *testing.T) {
	for _, test := range []struct {
		name   string
		status int
		code   string
		target error
	}{
		{"limit", http.StatusTooManyRequests, "LIMIT_EXCEEDED", ErrLimitExceeded},
		{"not found", http.StatusNotFound, "RESERVATION_NOT_FOUND", ErrReservationNotFound},
		{"feature disabled", http.StatusForbidden, "FEATURE_NOT_ENABLED", ErrFeatureNotEnabled},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /reservations/id": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(test.status)
					writeJSON(t, w, map[string]any{
						"code": test.code, "source": "API", "message": "server failure", "limit": "max_mac_reservations",
					})
				},
			})
			service := newTestClient(t, fake.Server).Reservations()
			// When
			_, err := service.Get(t.Context(), "id")
			// Then
			require.ErrorIs(t, err, test.target)
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, test.code, sdkErr.Code)
			require.Equal(t, "API", sdkErr.Source)
			require.Equal(t, test.status, sdkErr.StatusCode)
		})
	}
}
