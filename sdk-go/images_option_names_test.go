// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestImageValidation_whenOptionsAreInvalidNamesGoField(t *testing.T) {
	for _, tt := range []struct {
		name string
		opts BuildImageOptions
	}{
		{"CPUArch", BuildImageOptions{CPUArch: "invalid"}},
		{"IdleExpiry", BuildImageOptions{IdleExpiry: time.Nanosecond}},
		{"Name", BuildImageOptions{Name: " "}},
		{"Region", BuildImageOptions{Region: "bad\nregion"}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given an invalid build option and no usable API routes.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When validating before building.
			_, err := client.Images().Build(t.Context(), NewImageBuilder("alpine"), &tt.opts)
			// Then the local error identifies the Go field, before any request.
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, tt.name)
			require.Empty(t, fake.requests())
		})
	}
}
