// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestParseOptionalExpiry_rejectsNonPositiveDuration(t *testing.T) {
	_, err := parseOptionalExpiry("0s")
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	_, err = parseOptionalExpiry("-1s")
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestParseOptionalExpiry_rejectsPastTimestamp(t *testing.T) {
	_, err := parseOptionalExpiry(time.Now().Add(-time.Minute).UTC().Format(time.RFC3339))
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestParseOptionalExpiry_acceptsFutureValues(t *testing.T) {
	byDuration, err := parseOptionalExpiry("1m")
	require.NoError(t, err)
	require.NotNil(t, byDuration)
	byTimestamp, err := parseOptionalExpiry(time.Now().Add(time.Minute).UTC().Format(time.RFC3339))
	require.NoError(t, err)
	require.NotNil(t, byTimestamp)
}
