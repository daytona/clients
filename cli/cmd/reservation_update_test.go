// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestReservationUpdate_refusesChargesWhenConsentMissing(t *testing.T) {
	// Given: the harness expects no requests, including credential resolution.
	// When
	_, _, err := runCLI(t, []string{"reservation", "update", "region-a:res-1", "--auto-renew=true"}, nil)
	// Then
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestReservationUpdate_sendsRequestWhenConsentSatisfied(t *testing.T) {
	for _, value := range []string{"true", "false"} {
		t.Run(value, func(t *testing.T) {
			// Given
			args := []string{"reservation", "update", "region-a:res-1", "--auto-renew=" + value}
			if value == "true" {
				args = append(args, "--yes")
			}
			// When
			_, _, err := runCLI(t, args, []expectedRequest{{
				method: http.MethodPatch, path: "/reservations/region-a:res-1", status: 200,
				bodyJSON: `{"auto_renew":` + value + `}`, response: macReservationFixture(`"m4-10c-16g"`, m4Capacity),
			}})
			// Then
			require.NoError(t, err)
		})
	}
}
