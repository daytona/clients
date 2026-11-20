// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestReservationsRejectMalformedFields(t *testing.T) {
	optional := map[string]bool{
		"model": true, "capacity": true, "end_reason": true, "renewal_error": true,
		"activated_at": true, "pending_mac_started_at": true, "period_started_at": true,
		"period_end": true, "reissued_at": true, "ended_at": true, "future_field": true,
	}
	for name := range reservationFixture() {
		if name == "future_field" {
			continue
		}
		for _, mutation := range []string{"type", "missing", "null"} {
			if optional[name] && mutation != "type" {
				continue
			}
			t.Run(name+"/"+mutation, func(t *testing.T) {
				// Given
				fixture := reservationFixture()
				switch mutation {
				case "type":
					fixture[name] = []any{}
				case "missing":
					delete(fixture, name)
				case "null":
					fixture[name] = nil
				}
				fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /reservations/id": jsonHandler(t, fixture)})
				client := newTestClient(t, fake.Server)
				// When
				_, err := client.Reservations().Get(t.Context(), "id")
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				var sdkErr *Error
				require.ErrorAs(t, err, &sdkErr)
				require.Equal(t, SourceSDK, sdkErr.Source)
			})
		}
	}
}

func TestReservationsOptionalFields(t *testing.T) {
	for _, null := range []bool{true, false} {
		t.Run(map[bool]string{true: "null", false: "omitted"}[null], func(t *testing.T) {
			// Given
			fixture := reservationFixture()
			for _, name := range []string{
				"model", "capacity", "end_reason", "renewal_error", "activated_at",
				"pending_mac_started_at", "period_started_at", "period_end", "reissued_at", "ended_at",
			} {
				delete(fixture, name)
				if null {
					fixture[name] = nil
				}
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /reservations/id": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			got, err := client.Reservations().Get(t.Context(), "id")
			// Then
			require.NoError(t, err)
			expected := expectedReservation()
			expected.Model, expected.RenewalError, expected.RawEndReason, expected.EndReason = "", "", "", ""
			expected.Capacity = nil
			expected.ActivatedAt, expected.PendingMacStartedAt, expected.PeriodStartedAt = time.Time{}, time.Time{}, time.Time{}
			expected.PeriodEnd, expected.ReissuedAt, expected.EndedAt = time.Time{}, time.Time{}, time.Time{}
			require.Equal(t, expected, *got)
		})
	}
}

func TestReservationsUnknownEnums(t *testing.T) {
	// Given
	fixture := reservationFixture()
	fixture["status"], fixture["origin"], fixture["end_reason"] = "next_status", "next_origin", "next_reason"
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /reservations/id": jsonHandler(t, fixture)})
	client := newTestClient(t, fake.Server)
	// When
	got, err := client.Reservations().Get(t.Context(), "id")
	// Then
	require.NoError(t, err)
	require.Equal(t, MacReservationStatusUnknown, got.Status)
	require.Equal(t, "next_status", got.RawStatus)
	require.Equal(t, MacReservationOriginUnknown, got.Origin)
	require.Equal(t, "next_origin", got.RawOrigin)
	require.Equal(t, MacReservationEndReasonUnknown, got.EndReason)
	require.Equal(t, "next_reason", got.RawEndReason)
}

func TestReservationsKnownEnums(t *testing.T) {
	for _, value := range []string{"pending_charge", "pending_mac", "active", "ending", "released", "failed"} {
		t.Run("status/"+value, func(t *testing.T) {
			// Given
			fixture := reservationFixture()
			fixture["status"] = value
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /reservations/id": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			got, err := client.Reservations().Get(t.Context(), "id")
			// Then
			require.NoError(t, err)
			require.Equal(t, MacReservationStatus(value), got.Status)
			require.Equal(t, value, got.RawStatus)
		})
	}
	for _, value := range []string{"expired", "released", "charge_failed", "not_accepted", "reissued", "operator"} {
		t.Run("end_reason/"+value, func(t *testing.T) {
			// Given
			fixture := reservationFixture()
			fixture["end_reason"], fixture["origin"] = value, "explicit"
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /reservations/id": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			got, err := client.Reservations().Get(t.Context(), "id")
			// Then
			require.NoError(t, err)
			require.Equal(t, MacReservationEndReason(value), got.EndReason)
			require.Equal(t, value, got.RawEndReason)
			require.Equal(t, MacReservationOriginExplicit, got.Origin)
		})
	}
}

func TestReservationsRejectBadTimestamps(t *testing.T) {
	for _, name := range []string{
		"created_at", "activated_at", "pending_mac_started_at", "period_started_at", "period_end", "reissued_at", "ended_at",
	} {
		for _, value := range []string{"nope", "2026-10-20", "2026-10-20T00:00:00", "2026-10-20T00:00:00+24:00"} {
			t.Run(name+"/"+value, func(t *testing.T) {
				// Given
				fixture := reservationFixture()
				fixture[name] = value
				fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /reservations/id": jsonHandler(t, fixture)})
				client := newTestClient(t, fake.Server)
				// When
				_, err := client.Reservations().Get(t.Context(), "id")
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.ErrorContains(t, err, name)
			})
		}
	}
}

func TestReservationsRejectBadNestedNumbers(t *testing.T) {
	for _, parent := range []string{"capacity", "resources_used"} {
		for _, name := range []string{"cores", "memory_mib", "disk_mib"} {
			for _, value := range []any{nil, "1", 1.5, 1e20} {
				t.Run(parent+"/"+name, func(t *testing.T) {
					// Given
					fixture := reservationFixture()
					fixture[parent] = map[string]any{"cores": 1, "memory_mib": 2, "disk_mib": 3, name: value}
					fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /reservations/id": jsonHandler(t, fixture)})
					client := newTestClient(t, fake.Server)
					// When
					_, err := client.Reservations().Get(t.Context(), "id")
					// Then
					require.ErrorIs(t, err, ErrBadRequest)
					require.ErrorContains(t, err, name)
				})
			}
		}
	}
}

func TestReservationsRejectDurationOverflow(t *testing.T) {
	// Given
	fixture := reservationFixture()
	fixture["period_seconds"] = int64(9223372037)
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /reservations/id": jsonHandler(t, fixture)})
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Reservations().Get(t.Context(), "id")
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorContains(t, err, "period_seconds")
}
