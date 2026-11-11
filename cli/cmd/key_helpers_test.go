// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestParseOptionalExpiry_rejectsInvalidDuration(t *testing.T) {
	for _, value := range []string{"0s", "-1s", "1500ms", "1ns"} {
		t.Run(value, func(t *testing.T) {
			// Given an invalid key lifetime.
			// When parsing the expiry.
			_, err := parseOptionalExpiry(value)
			// Then the caller receives a usage error.
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
		})
	}
}

func TestParseOptionalExpiry_rejectsPastTimestamp(t *testing.T) {
	// Given a timestamp in the past.
	value := "2000-01-01T00:00:00Z"
	// When parsing the expiry.
	_, err := parseOptionalExpiry(value)
	// Then it is refused locally.
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestParseOptionalExpiry_acceptsFutureValues(t *testing.T) {
	for _, value := range []string{"1m", "1000ms", "2099-01-01T00:00:00Z"} {
		t.Run(value, func(t *testing.T) {
			// Given a valid future expiry.
			// When parsing it.
			expiry, err := parseOptionalExpiry(value)
			// Then it produces a future timestamp.
			require.NoError(t, err)
			require.True(t, expiry.After(time.Now()))
		})
	}
}
