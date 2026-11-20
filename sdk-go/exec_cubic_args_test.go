// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestExecArgs_whenExecutableEmpty_rejectsBeforeRequest(t *testing.T) {
	for _, name := range []string{"ExecArgs", "RunArgs", "StartArgs"} {
		t.Run(name, func(t *testing.T) {
			// Given an argv containing arguments but no executable.
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"POST /processes": func(w http.ResponseWriter, _ *http.Request) {
					w.WriteHeader(http.StatusInternalServerError)
				},
			})
			var err error
			// When using any shell-free entry point.
			switch name {
			case "ExecArgs":
				_, err = sandbox.ExecArgs(t.Context(), []string{"", "argument"}, nil)
			case "RunArgs":
				_, err = sandbox.Process().RunArgs(t.Context(), []string{"", "argument"}, nil)
			case "StartArgs":
				_, err = sandbox.Process().StartArgs(t.Context(), []string{"", "argument"}, nil)
			}
			// Then invalid input is refused locally.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}
