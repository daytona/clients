// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"crypto/rand"
	"math"
	"net/http"
	"unicode/utf8"
)

type reservationPurchase struct {
	Region    string `json:"region_id"`
	Model     string `json:"model,omitempty"`
	AutoRenew *bool  `json:"auto_renew,omitempty"`
	Count     int    `json:"count,omitempty"`
	MinCount  int    `json:"min_count,omitempty"`
}

// Create purchases one Mac in region. Blank region is sent unchanged.
// Nil options select the region's default model and organization's renewal policy.
// Reuse an explicit idempotency key after a lost response to avoid buying twice.
// ErrNoMacAvailable means no matching Mac is free; ErrLimitExceeded reports the
// organization's reservation limit. This call does not wait for activation.
func (s *Reservations) Create(
	ctx context.Context, region string, opts *CreateReservationOptions,
) (*MacReservation, error) {
	options := CreateReservationOptions{}
	if opts != nil {
		options = *opts
	}
	body := reservationPurchase{Region: region, Model: options.Model, AutoRenew: options.AutoRenew}
	if err := checkReservationPurchase(body); err != nil {
		return nil, err
	}
	key, err := reservationKey(options.IdempotencyKey)
	if err != nil {
		return nil, err
	}
	return s.read(ctx, apiRequest{Method: http.MethodPost, Path: "/reservations", Body: body, IdempotencyKey: key})
}

// CreateBulk purchases count Macs of one model in region. Count must be 1–100.
// MinCount defaults to count (all or nothing). A partial purchase is allowed
// only when at least MinCount Macs fit both availability and organization limits.
// ErrLimitExceeded is checked by the server before ErrNoMacAvailable.
// Reuse an explicit idempotency key after a lost response to avoid buying twice.
// Invalid counts or header values return ErrBadRequest before any request.
func (s *Reservations) CreateBulk(
	ctx context.Context, region string, count int, opts *CreateReservationsBulkOptions,
) (*MacReservationBatch, error) {
	options := CreateReservationsBulkOptions{}
	if opts != nil {
		options = *opts
	}
	body := reservationPurchase{
		Region: region, Model: options.Model, AutoRenew: options.AutoRenew,
		Count: count, MinCount: options.MinCount,
	}
	if err := checkReservationPurchase(body); err != nil {
		return nil, err
	}
	if count < 1 || count > 100 {
		return nil, badRequest("count must be an integer from 1 to 100")
	}
	if options.MinCount < 0 || options.MinCount > count {
		return nil, badRequest("MinCount must be 0 (defaults to count) or 1 to count")
	}
	key, err := reservationKey(options.IdempotencyKey)
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	if requestErr := s.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: "/reservations/bulk", Body: body, IdempotencyKey: key, Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	return parseReservationBatch(raw)
}

func checkReservationPurchase(body reservationPurchase) error {
	if !utf8.ValidString(body.Region) || !utf8.ValidString(body.Model) {
		return badRequest("region and Model must be valid UTF-8")
	}
	return nil
}

func reservationKey(key string) (string, error) {
	if key == "" {
		return rand.Text(), nil
	}
	if err := headerText(key, "IdempotencyKey"); err != nil {
		return "", err
	}
	return key, nil
}

// parseReservationBatch reads a bulk purchase response; its counts are the
// contract's int32 values.
func parseReservationBatch(raw JSONValue) (*MacReservationBatch, error) {
	object, err := parseObject(raw, "reservation batch")
	if err != nil {
		return nil, err
	}
	batch := &MacReservationBatch{}
	for _, entry := range []struct {
		name   string
		target *int64
	}{{"requested", &batch.Requested}, {"reserved", &batch.Reserved}} {
		value, parseErr := integerField(object, entry.name, true)
		if parseErr != nil {
			return nil, parseErr
		}
		if value < math.MinInt32 || value > math.MaxInt32 {
			return nil, badRequest("response field " + entry.name + " must be a 32-bit integer")
		}
		*entry.target = value
	}
	rows, err := field[[]JSONValue](object, "reservations", true)
	if err != nil {
		return nil, err
	}
	batch.Reservations, err = parseReservations(rows)
	if err != nil {
		return nil, err
	}
	return batch, nil
}
