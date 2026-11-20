// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestAccountValidation_whenOptionsAreInvalidNamesGoField(t *testing.T) {
	for _, tt := range []struct {
		name string
		call func(*Client) error
	}{
		{"AllowedDomains", func(c *Client) error {
			_, err := c.Secrets().Set(t.Context(), "TOKEN", "value", nil)
			return err
		}},
		{"AllowedDomains", func(c *Client) error {
			_, err := c.Secrets().Set(t.Context(), "TOKEN", "value",
				&SetSecretOptions{AllowedDomains: []string{"\xff"}})
			return err
		}},
		{"MinCount", func(c *Client) error {
			_, err := c.Reservations().CreateBulk(t.Context(), "region", 1,
				&CreateReservationsBulkOptions{MinCount: 2})
			return err
		}},
		{"IdempotencyKey", func(c *Client) error {
			_, err := c.Reservations().Create(t.Context(), "region",
				&CreateReservationOptions{IdempotencyKey: "bad\nkey"})
			return err
		}},
		{"SizeGiB", func(c *Client) error {
			_, err := c.Volumes().Create(t.Context(), "volume", &CreateVolumeOptions{SizeGiB: -1})
			return err
		}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given an invalid Go option and no usable API routes.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When validating the operation.
			err := tt.call(client)
			// Then the local error identifies the Go field, before any request.
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, tt.name)
			require.Empty(t, fake.requests())
		})
	}
}
