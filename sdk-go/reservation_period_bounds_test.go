// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"net/http"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReservationPeriodNUsesInt32Bounds(t *testing.T) {
	for _, value := range []int64{math.MinInt32 - 1, math.MinInt32, math.MaxInt32, math.MaxInt32 + 1} {
		t.Run(strconv.FormatInt(value, 10), func(t *testing.T) {
			// Given a period count on either side of the contract's int32 bounds.
			wire := reservationFixture()
			wire["period_n"] = value
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /reservations/id": jsonHandler(t, wire),
			})
			client := newTestClient(t, fake.Server)
			// When reading the reservation.
			result, err := client.Reservations().Get(t.Context(), "id")
			// Then only values representable by the contract's type are returned.
			if value < math.MinInt32 || value > math.MaxInt32 {
				require.ErrorIs(t, err, ErrBadRequest)
				require.ErrorContains(t, err, "period_n")
				return
			}
			require.NoError(t, err)
			require.Equal(t, value, result.PeriodN)
		})
	}
}
