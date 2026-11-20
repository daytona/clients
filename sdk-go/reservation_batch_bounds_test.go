// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReservationBatch_whenCountsLeaveInt32_rejectsResponse(t *testing.T) {
	for name, counts := range map[string]map[string]any{
		"requested": {"requested": 2147483648, "reserved": 1},
		"reserved":  {"requested": 1, "reserved": -2147483649},
	} {
		t.Run(name, func(t *testing.T) {
			// Given a batch response whose count leaves the contract's int32
			body := map[string]any{"reservations": []any{reservationFixture()}}
			for key, value := range counts {
				body[key] = value
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /reservations/bulk": jsonHandler(t, body),
			})
			service := newTestClient(t, fake.Server).Reservations()
			// When
			_, err := service.CreateBulk(t.Context(), "region-a", 1, &CreateReservationsBulkOptions{Model: "custom"})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Contains(t, err.Error(), "response field "+name+" must be a 32-bit integer")
		})
	}
}
