// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReservationsRejectInvalidInputBeforeRequests(t *testing.T) {
	tests := map[string]func(*Reservations) error{
		"count zero":         func(s *Reservations) error { _, err := s.CreateBulk(t.Context(), "", 0, nil); return err },
		"count negative":     func(s *Reservations) error { _, err := s.CreateBulk(t.Context(), "", -1, nil); return err },
		"count over maximum": func(s *Reservations) error { _, err := s.CreateBulk(t.Context(), "", 101, nil); return err },
		"min count negative": func(s *Reservations) error {
			_, err := s.CreateBulk(t.Context(), "", 2, &CreateReservationsBulkOptions{MinCount: -1})
			return err
		},
		"min count over count": func(s *Reservations) error {
			_, err := s.CreateBulk(t.Context(), "", 2, &CreateReservationsBulkOptions{MinCount: 3})
			return err
		},
		"bad region": func(s *Reservations) error { _, err := s.Create(t.Context(), "\xff", nil); return err },
		"bad model": func(s *Reservations) error {
			_, err := s.Create(t.Context(), "", &CreateReservationOptions{Model: "\xff"})
			return err
		},
		"bad bulk region": func(s *Reservations) error { _, err := s.CreateBulk(t.Context(), "\xff", 1, nil); return err },
		"bad bulk model": func(s *Reservations) error {
			_, err := s.CreateBulk(t.Context(), "", 1, &CreateReservationsBulkOptions{Model: "\xff"})
			return err
		},
		"negative limit": func(s *Reservations) error {
			_, err := s.List(t.Context(), &ListReservationsOptions{Limit: -1})
			return err
		},
		"large limit": func(s *Reservations) error {
			_, err := s.List(t.Context(), &ListReservationsOptions{Limit: 201})
			return err
		},
		"bad cursor": func(s *Reservations) error {
			_, err := s.List(t.Context(), &ListReservationsOptions{Cursor: "\xff"})
			return err
		},
	}
	for _, id := range []string{"", " ", ".", "..", "\xff"} {
		tests["get id "+id] = func(s *Reservations) error { _, err := s.Get(t.Context(), id); return err }
		tests["renew id "+id] = func(s *Reservations) error { _, err := s.Renew(t.Context(), id, nil); return err }
		tests["update id "+id] = func(s *Reservations) error { _, err := s.Update(t.Context(), id, true); return err }
	}
	for _, key := range []string{" key", "key ", "key\n", "key\t", "clé", "\x7f"} {
		tests["create key "+key] = func(s *Reservations) error {
			_, err := s.Create(t.Context(), "", &CreateReservationOptions{IdempotencyKey: key})
			return err
		}
		tests["bulk key "+key] = func(s *Reservations) error {
			_, err := s.CreateBulk(t.Context(), "", 1, &CreateReservationsBulkOptions{IdempotencyKey: key})
			return err
		}
		tests["renew key "+key] = func(s *Reservations) error {
			_, err := s.Renew(t.Context(), "id", &RenewReservationOptions{IdempotencyKey: key})
			return err
		}
	}
	for name, call := range tests {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			service := newTestClient(t, fake.Server).Reservations()
			// When
			err := call(service)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, SourceSDK, sdkErr.Source)
			require.Empty(t, fake.requests())
		})
	}
}

func TestReservationsErrorsAndCancellation(t *testing.T) {
	for _, operation := range []string{"get", "create", "bulk", "list", "renew", "update"} {
		t.Run(operation+"/server", func(t *testing.T) {
			// Given
			handler := func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusConflict)
				writeJSON(t, w, map[string]any{"code": "NO_MAC_AVAILABLE", "source": "API", "message": "no capacity"})
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{"/reservations": handler, "/reservations/": handler})
			service := newTestClient(t, fake.Server).Reservations()
			// When
			_, err := reservationOperation(t, service, operation)
			// Then
			require.ErrorIs(t, err, ErrNoMacAvailable)
			require.ErrorIs(t, err, ErrConflict)
		})
		t.Run(operation+"/canceled", func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			service := newTestClient(t, fake.Server).Reservations()
			ctx, cancel := context.WithCancel(t.Context())
			cancel()
			// When
			var err error
			switch operation {
			case "get":
				_, err = service.Get(ctx, "id")
			case "create":
				_, err = service.Create(ctx, "", nil)
			case "bulk":
				_, err = service.CreateBulk(ctx, "", 1, nil)
			case "list":
				_, err = service.List(ctx, nil)
			case "renew":
				_, err = service.Renew(ctx, "id", nil)
			case "update":
				_, err = service.Update(ctx, "id", false)
			}
			// Then
			require.ErrorIs(t, err, context.Canceled)
			require.Empty(t, fake.requests())
		})
	}
}

func TestReservationsMalformedEnvelopes(t *testing.T) {
	for _, operation := range []string{"get", "list", "bulk"} {
		for _, body := range []any{
			nil,
			[]any{},
			"invalid",
			map[string]any{},
			map[string]any{"reservations": nil},
			map[string]any{"reservations": []any{nil}},
			map[string]any{"reservations": []any{"invalid"}},
			map[string]any{"reservations": []any{reservationFixture()}, "next_cursor": 1},
		} {
			t.Run(operation, func(t *testing.T) {
				// Given
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"/reservations": jsonHandler(t, body), "/reservations/": jsonHandler(t, body),
				})
				service := newTestClient(t, fake.Server).Reservations()
				// When
				_, err := reservationOperation(t, service, operation)
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
			})
		}
	}
}

func TestReservationsRejectMalformedBatchCounts(t *testing.T) {
	for _, body := range []any{
		map[string]any{"requested": 1.5, "reserved": 1, "reservations": []any{}},
		map[string]any{"requested": 1, "reserved": "1", "reservations": []any{}},
		map[string]any{"requested": 1, "reservations": []any{}},
	} {
		t.Run("counts", func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /reservations/bulk": jsonHandler(t, body)})
			service := newTestClient(t, fake.Server).Reservations()
			// When
			_, err := service.CreateBulk(t.Context(), "", 1, nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
