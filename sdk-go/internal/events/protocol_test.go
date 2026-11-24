// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package events

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestDecoderAcceptsContractFramesWhenProtocolAddsUnknownFields(t *testing.T) {
	// Given
	data, err := os.ReadFile("../../../contract/sandbox-events-protocol.json")
	if errors.Is(err, os.ErrNotExist) && os.Getenv("DAYTONA_SDK_GO_REQUIRE_FIXTURES") == "" {
		t.Skip("protocol drift requires the repository's contract fixture")
	}
	require.NoError(t, err)
	var contract struct {
		ServerFrames []struct {
			Name  string          `json:"name"`
			Frame json.RawMessage `json:"frame"`
		} `json:"server_frames"`
	}
	require.NoError(t, json.Unmarshal(data, &contract))
	for _, example := range contract.ServerFrames {
		t.Run(example.Name, func(t *testing.T) {
			// When
			_, valid := decodeFrame(example.Frame)

			// Then
			require.True(t, valid)
		})
	}
}

func TestPackingRespectsByteAndIDLimitsWhenIDsNeedEscaping(t *testing.T) {
	// Given
	ids := make([]subscription, 2500)
	for i := range ids {
		ids[i] = subscription{ID: strings.Repeat(`"<>&`, 30)}
	}
	for _, kind := range []string{"subscribe", "unsubscribe"} {
		t.Run(kind, func(t *testing.T) {
			// When
			frames, err := subscriptionFrames(kind, ids)

			// Then
			require.NoError(t, err)
			total := 0
			for _, data := range frames {
				require.LessOrEqual(t, len(data), maxClientFrame)
				var f struct {
					IDs []json.RawMessage `json:"ids"`
				}
				require.NoError(t, json.Unmarshal(data, &f))
				require.LessOrEqual(t, len(f.IDs), maxFrameIDs)
				total += len(f.IDs)
			}
			require.Equal(t, len(ids), total)
		})
	}
}

func TestDecoderRejectsOversizedAndDeepFramesWhenParsing(t *testing.T) {
	for name, payload := range map[string]string{
		"oversized utf8": `{"type":"ping","extension":"` + strings.Repeat("界", maxServerFrame/3) + `"}`,
		"too deep":       `{"type":"ping","extension":` + strings.Repeat("[", 600) + "0" + strings.Repeat("]", 600) + "}",
		"invalid utf8":   "{\"type\":\"ping\",\"extension\":\"\xff\"}",
		"unknown type":   `{"type":"future"}`,
	} {
		t.Run(name, func(t *testing.T) {
			// Given / When
			_, valid := decodeFrame([]byte(payload))

			// Then
			require.False(t, valid)
		})
	}
}
