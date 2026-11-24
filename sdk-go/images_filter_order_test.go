// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestArtifactList_whenBothFiltersAreInvalid(t *testing.T) {
	for _, service := range []struct {
		name string
		list func(*Client) error
	}{
		{"images", func(client *Client) error {
			_, err := client.Images().List(t.Context(), &ListImagesOptions{Name: "\xff", SandboxID: "\xfe"})
			return err
		}},
		{"snapshots", func(client *Client) error {
			_, err := client.Snapshots().List(t.Context(), &ListSnapshotsOptions{Name: "\xff", SandboxID: "\xfe"})
			return err
		}},
	} {
		t.Run(service.name, func(t *testing.T) {
			// Given two invalid filters on either shared artifact-list surface.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			for range 256 {
				// When listing repeatedly to exercise validation ordering.
				err := service.list(client)

				// Then name is always rejected first, without an HTTP request.
				require.ErrorIs(t, err, ErrBadRequest)
				var detail *Error
				require.ErrorAs(t, err, &detail)
				require.Equal(t, "name must be valid UTF-8", detail.Message)
			}
			require.Empty(t, fake.requests())
		})
	}
}
