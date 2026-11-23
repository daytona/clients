// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestListProcesses_whenStateFilterIsTyped(t *testing.T) {
	for _, state := range []ProcessListState{
		"", ProcessListStateAll, ProcessListStateRunning, ProcessListStateTerminal,
	} {
		t.Run(string(state), func(t *testing.T) {
			// Given an empty process list.
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"GET /processes": jsonHandler(t, map[string][]ProcessInfo{"items": {}}),
			})

			// When listing with a typed state filter.
			_, err := sandbox.Process().List(t.Context(), &ListProcessesOptions{State: state})

			// Then the selected filter reaches the server unchanged.
			require.NoError(t, err)
			require.Equal(t, string(state), fake.requests()[0].Query.Get("state"))
		})
	}
}
