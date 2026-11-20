// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"iter"
	"net/http"
)

// Reservations manages dedicated Macs billed in 24-hour periods.
// Obtain it from Client.Reservations. Purchases, renewals and enabling auto-renew
// need macOS access (ErrFeatureNotEnabled); reads and disabling auto-renew do not.
type Reservations struct{ client *Client }

// Reservations returns the client's concurrency-safe reservation service.
func (c *Client) Reservations() *Reservations { return &Reservations{client: c} }

// Get reads one reservation. A missing reservation returns ErrReservationNotFound.
// Invalid path identifiers return ErrBadRequest before any request.
func (s *Reservations) Get(ctx context.Context, id string) (*MacReservation, error) {
	segment, err := pathSegment(id)
	if err != nil {
		return nil, err
	}
	return s.read(ctx, apiRequest{Path: "/reservations/" + segment})
}

// List returns exactly one page of the organization's reservations.
// Invalid cursor encoding or a limit outside 1–200 returns ErrBadRequest;
// zero Limit selects the server default.
func (s *Reservations) List(ctx context.Context, opts *ListReservationsOptions) (*Page[MacReservation], error) {
	options := ListReservationsOptions{}
	if opts != nil {
		options = *opts
	}
	query, err := pageQuery(options.Cursor, options.Limit)
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	if requestErr := s.client.api().do(ctx, apiRequest{
		Path: "/reservations", Query: query, Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	object, err := parseObject(raw, "reservation list")
	if err != nil {
		return nil, err
	}
	rows, err := field[[]JSONValue](object, "reservations", true)
	if err != nil {
		return nil, err
	}
	cursor, err := field[string](object, "next_cursor", false)
	if err != nil {
		return nil, err
	}
	items, err := parseReservations(rows)
	if err != nil {
		return nil, err
	}
	return &Page[MacReservation]{Items: items, NextCursor: cursor}, nil
}

// ListAll lazily traverses reservations, stopping on cancellation, an error,
// exhaustion or a repeated cursor. Options are copied now; breaking iteration
// makes no further requests. An error is yielded once.
func (s *Reservations) ListAll(ctx context.Context, opts *ListReservationsOptions) iter.Seq2[MacReservation, error] {
	options := ListReservationsOptions{}
	if opts != nil {
		options = *opts
	}
	return paginate(ctx, options.Cursor, func(ctx context.Context, cursor string) (*Page[MacReservation], error) {
		next := options
		next.Cursor = cursor
		return s.List(ctx, &next)
	})
}

// Renew pays for the reservation's next period. Reuse an explicit idempotency
// key with identical input after a lost response to avoid paying twice.
// ErrLimitExceeded reports the organization's reservation limit.
func (s *Reservations) Renew(ctx context.Context, id string, opts *RenewReservationOptions) (*MacReservation, error) {
	segment, err := pathSegment(id)
	if err != nil {
		return nil, err
	}
	key := ""
	if opts != nil {
		key = opts.IdempotencyKey
	}
	key, err = reservationKey(key)
	if err != nil {
		return nil, err
	}
	return s.read(ctx, apiRequest{
		Method: http.MethodPost, Path: "/reservations/" + segment + "/renew", IdempotencyKey: key,
	})
}

// Update enables or disables automatic renewal. Disabling it does not require
// macOS access. A reservation that is no longer live returns ErrConflict.
func (s *Reservations) Update(ctx context.Context, id string, autoRenew bool) (*MacReservation, error) {
	segment, err := pathSegment(id)
	if err != nil {
		return nil, err
	}
	body := struct {
		AutoRenew bool `json:"auto_renew"`
	}{AutoRenew: autoRenew}
	return s.read(ctx, apiRequest{Method: http.MethodPatch, Path: "/reservations/" + segment, Body: body})
}

func (s *Reservations) read(ctx context.Context, request apiRequest) (*MacReservation, error) {
	var raw JSONValue
	request.Result = &raw
	if err := s.client.api().do(ctx, request); err != nil {
		return nil, err
	}
	return parseReservation(raw)
}
