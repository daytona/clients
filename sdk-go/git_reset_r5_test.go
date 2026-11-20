// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestGitReset_whenModeIsTyped(t *testing.T) {
	for _, mode := range []GitResetMode{
		"", GitResetModeMixed, GitResetModeSoft, GitResetModeHard, GitResetModeMerge, GitResetModeKeep, "future",
	} {
		t.Run(string(mode), func(t *testing.T) {
			// Given a successful reset endpoint.
			git, fake := gitTestService(t, map[string]http.HandlerFunc{
				"POST /toolbox/sbx/git/reset": jsonHandler(t, struct{}{}),
			})

			// When resetting with a typed mode, including an extension.
			err := git.Reset(t.Context(), "/repo", &GitResetOptions{Mode: mode})

			// Then the mode is preserved, with the zero value still omitted.
			require.NoError(t, err)
			var body map[string]json.RawMessage
			require.NoError(t, json.Unmarshal(fake.requests()[0].Body, &body))
			if mode == "" {
				require.NotContains(t, body, "mode")
				return
			}
			var sent string
			require.NoError(t, json.Unmarshal(body["mode"], &sent))
			require.Equal(t, string(mode), sent)
		})
	}
}
