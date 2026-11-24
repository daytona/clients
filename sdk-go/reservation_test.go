// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func reservationFixture() map[string]any {
	return map[string]any{
		"id": "region-a:reservation-1", "org_id": "org", "project_id": "project", "region_id": "region-a",
		"status": "released", "origin": "auto", "auto_renew": false, "slots_used": 2, "model": "m4-10c-16g",
		"capacity":       map[string]any{"cores": 10, "memory_mib": 16384, "disk_mib": 235520},
		"resources_used": map[string]any{"cores": 5, "memory_mib": 8192, "disk_mib": 117760},
		"period_n":       3, "period_seconds": 86400, "created_at": "2026-10-20T00:00:00Z",
		"pending_mac_started_at": "2026-10-20T00:01:00Z", "activated_at": "2026-10-20T00:02:00Z",
		"period_started_at": "2026-10-22T00:02:00Z", "period_end": "2026-10-23T00:02:00Z",
		"reissued_at": "2026-10-21T00:00:00Z", "ended_at": "2026-10-23T00:02:01Z",
		"end_reason": "expired", "renewal_pending": true, "renewal_error": "budget held",
		"future_field": true,
	}
}

func expectedReservation() MacReservation {
	return MacReservation{
		ID: "region-a:reservation-1", OrgID: "org", ProjectID: "project", Region: "region-a",
		AutoRenew: false, SlotsUsed: 2, Model: "m4-10c-16g",
		Capacity:      &MacResources{Cores: 10, MemoryMiB: 16384, DiskMiB: 235520},
		ResourcesUsed: MacResources{Cores: 5, MemoryMiB: 8192, DiskMiB: 117760},
		PeriodN:       3, Period: 24 * time.Hour,
		CreatedAt:           time.Date(2026, 10, 20, 0, 0, 0, 0, time.UTC),
		PendingMacStartedAt: time.Date(2026, 10, 20, 0, 1, 0, 0, time.UTC),
		ActivatedAt:         time.Date(2026, 10, 20, 0, 2, 0, 0, time.UTC),
		PeriodStartedAt:     time.Date(2026, 10, 22, 0, 2, 0, 0, time.UTC),
		PeriodEnd:           time.Date(2026, 10, 23, 0, 2, 0, 0, time.UTC),
		ReissuedAt:          time.Date(2026, 10, 21, 0, 0, 0, 0, time.UTC),
		EndedAt:             time.Date(2026, 10, 23, 0, 2, 1, 0, time.UTC),
		RenewalPending:      true, RenewalError: "budget held",
		Status: MacReservationStatusReleased, RawStatus: "released",
		Origin: MacReservationOriginAuto, RawOrigin: "auto",
		EndReason: MacReservationEndReasonExpired, RawEndReason: "expired",
	}
}

func TestReservationsMapsEveryField(t *testing.T) {
	for _, operation := range []string{"get", "create", "bulk", "list", "renew", "update"} {
		t.Run(operation, func(t *testing.T) {
			// Given
			fixture := reservationFixture()
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"/reservations": func(w http.ResponseWriter, r *http.Request) {
					switch {
					case r.URL.Path == "/reservations/bulk":
						writeJSON(t, w, map[string]any{"requested": 1, "reserved": 1, "reservations": []any{fixture}})
					case r.Method == http.MethodGet && r.URL.Path == "/reservations":
						writeJSON(t, w, map[string]any{"reservations": []any{fixture}, "next_cursor": nil})
					default:
						writeJSON(t, w, fixture)
					}
				},
				"/reservations/": func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path == "/reservations/bulk" {
						writeJSON(t, w, map[string]any{"requested": 1, "reserved": 1, "reservations": []any{fixture}})
						return
					}
					writeJSON(t, w, fixture)
				},
			})
			service := newTestClient(t, fake.Server).Reservations()
			// When
			got, err := reservationOperation(t, service, operation)
			// Then
			require.NoError(t, err)
			require.Equal(t, expectedReservation(), *got)
		})
	}
}

func reservationOperation(t *testing.T, service *Reservations, operation string) (*MacReservation, error) {
	t.Helper()
	ctx := t.Context()
	switch operation {
	case "get":
		return service.Get(ctx, "reservation")
	case "create":
		return service.Create(ctx, "region-a", nil)
	case "renew":
		return service.Renew(ctx, "reservation", nil)
	case "update":
		return service.Update(ctx, "reservation", false)
	case "list":
		page, err := service.List(ctx, nil)
		if err != nil {
			return nil, err
		}
		require.Len(t, page.Items, 1)
		return &page.Items[0], nil
	case "bulk":
		batch, err := service.CreateBulk(ctx, "region-a", 1, nil)
		if err != nil {
			return nil, err
		}
		require.EqualValues(t, 1, batch.Requested)
		require.EqualValues(t, 1, batch.Reserved)
		require.Len(t, batch.Reservations, 1)
		return &batch.Reservations[0], nil
	default:
		t.Fatalf("unknown operation %s", operation)
		return nil, badRequest("unknown test operation")
	}
}

func TestReservationsMutatingRequests(t *testing.T) {
	for _, operation := range []string{"create", "bulk", "renew", "update"} {
		t.Run(operation, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"/reservations": jsonHandler(t, reservationFixture()),
				"/reservations/": func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path == "/reservations/bulk" {
						writeJSON(t, w, map[string]any{"requested": 3, "reserved": 2, "reservations": []any{reservationFixture()}})
						return
					}
					writeJSON(t, w, reservationFixture())
				},
			})
			service := newTestClient(t, fake.Server).Reservations()
			autoRenew := false
			// When
			var err error
			switch operation {
			case "create":
				_, err = service.Create(t.Context(), "", &CreateReservationOptions{
					Model: "custom", AutoRenew: &autoRenew, IdempotencyKey: "stable-key",
				})
			case "bulk":
				_, err = service.CreateBulk(t.Context(), "region-a", 3, &CreateReservationsBulkOptions{
					Model: "custom", AutoRenew: &autoRenew, MinCount: 2, IdempotencyKey: "stable-key",
				})
			case "renew":
				_, err = service.Renew(t.Context(), "id /?#", &RenewReservationOptions{IdempotencyKey: "stable-key"})
			case "update":
				_, err = service.Update(t.Context(), "id /?#", false)
			}
			// Then
			require.NoError(t, err)
			requests := fake.requests()
			require.Len(t, requests, 1)
			request := requests[0]
			require.Empty(t, request.Query)
			if operation == "update" {
				require.Equal(t, http.MethodPatch, request.Method)
				require.Equal(t, "/reservations/id%20%2F%3F%23", request.Path)
				require.Empty(t, request.Header.Get("Idempotency-Key"))
				require.JSONEq(t, `{"auto_renew":false}`, string(request.Body))
				return
			}
			require.Equal(t, http.MethodPost, request.Method)
			require.Equal(t, "stable-key", request.Header.Get("Idempotency-Key"))
			switch operation {
			case "create":
				require.Equal(t, "/reservations", request.Path)
				require.JSONEq(t, `{"region_id":"","model":"custom","auto_renew":false}`, string(request.Body))
			case "bulk":
				require.Equal(t, "/reservations/bulk", request.Path)
				require.JSONEq(t, `{"region_id":"region-a","count":3,"min_count":2,"model":"custom","auto_renew":false}`, string(request.Body))
			case "renew":
				require.Equal(t, "/reservations/id%20%2F%3F%23/renew", request.Path)
				require.Empty(t, request.Body)
			}
			require.NotEmpty(t, request.Header.Get("Authorization"))
			require.Equal(t, "sdk-go", request.Header.Get("X-Daytona-Source"))
		})
	}
}

func TestReservationsDefaultPurchasesGenerateFreshKeys(t *testing.T) {
	for _, operation := range []string{"create", "bulk", "renew"} {
		t.Run(operation, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"/reservations": jsonHandler(t, reservationFixture()),
				"/reservations/": func(w http.ResponseWriter, r *http.Request) {
					if r.URL.Path == "/reservations/bulk" {
						writeJSON(t, w, map[string]any{"requested": 1, "reserved": 1, "reservations": []any{reservationFixture()}})
						return
					}
					writeJSON(t, w, reservationFixture())
				},
			})
			service := newTestClient(t, fake.Server).Reservations()
			// When: independent purchases must never share generated keys.
			for range 2 {
				_, err := reservationOperation(t, service, operation)
				require.NoError(t, err)
			}
			// Then
			requests := fake.requests()
			require.Len(t, requests, 2)
			require.NotEmpty(t, requests[0].Header.Get("Idempotency-Key"))
			require.NotEqual(t, requests[0].Header.Get("Idempotency-Key"), requests[1].Header.Get("Idempotency-Key"))
			switch operation {
			case "create":
				require.JSONEq(t, `{"region_id":"region-a"}`, string(requests[0].Body))
			case "bulk":
				require.JSONEq(t, `{"region_id":"region-a","count":1}`, string(requests[0].Body))
			}
		})
	}
}
