// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestParseLifecycle_whenSecondsLeaveInt32_rejectsResponse(t *testing.T) {
	cases := map[string]string{
		"timeout above int32":     `{"timeout_seconds": 2147483648}`,
		"idle expiry below int32": `{"idle_expiry_seconds": -2147483649}`,
	}
	for name, raw := range cases {
		t.Run(name, func(t *testing.T) {
			// When
			_, err := parseLifecycle(json.RawMessage(raw))

			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Contains(t, err.Error(), "must be an int32")
		})
	}
}

func TestParseLifecycle_whenSecondsFitInt32_keepsDurations(t *testing.T) {
	// When
	policy, err := parseLifecycle(json.RawMessage(`{"timeout_seconds": 2147483647, "idle_expiry_seconds": -1}`))

	// Then
	require.NoError(t, err)
	require.Equal(t, 2147483647*time.Second, *policy.Timeout)
	require.Equal(t, -time.Second, *policy.IdleExpiry)
}

func TestParseLifecycle_whenSeveralFieldsAreMalformed_namesTheSameOne(t *testing.T) {
	// Given a response whose two duration fields are both malformed
	raw := json.RawMessage(`{"timeout_seconds": "x", "idle_expiry_seconds": "y"}`)

	// When it is parsed repeatedly
	_, first := parseLifecycle(raw)
	require.Error(t, first)
	for range 20 {
		_, err := parseLifecycle(raw)

		// Then the error always names the same field
		require.Equal(t, first.Error(), err.Error())
	}
}
