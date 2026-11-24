// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"errors"
	"os"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestWaitEarlyProbe_whenContractChanges_detectsDelayDrift(t *testing.T) {
	// Given
	data, err := os.ReadFile("../contract/sandbox-events-protocol.json")
	if errors.Is(err, os.ErrNotExist) && os.Getenv("DAYTONA_SDK_GO_REQUIRE_FIXTURES") == "" {
		t.Skip("early probe drift requires the repository's contract fixture")
	}
	require.NoError(t, err)
	var contract struct {
		ClientRules struct {
			EarlyProbe struct {
				DelayMS int64 `json:"delay_ms"`
			} `json:"early_probe"`
		} `json:"client_rules"`
	}

	// When
	require.NoError(t, json.Unmarshal(data, &contract))

	// Then
	require.Equal(t, contract.ClientRules.EarlyProbe.DelayMS, waitEarlyProbe.Milliseconds())
}
