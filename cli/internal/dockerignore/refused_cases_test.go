// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package dockerignore

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestNew_whenSharedSDKRefusedCase(t *testing.T) {
	// Given the ignore files both SDK suites expect to be refused.
	data, err := os.ReadFile("../../../sdk-python/tests/fixtures/dockerignore-refused.json")
	require.NoError(t, err)
	var cases []struct {
		Ignore string `json:"ignore"`
		Error  string `json:"error"`
	}
	require.NoError(t, json.Unmarshal(data, &cases))
	for _, fixture := range cases {
		t.Run(fixture.Ignore, func(t *testing.T) {
			patterns, err := ReadPatterns(strings.NewReader(fixture.Ignore))
			require.NoError(t, err)
			// When compiled by Go's reference implementation.
			_, err = New(patterns)
			// Then it is refused with the message both SDKs use.
			require.EqualError(t, err, fixture.Error)
		})
	}
}
