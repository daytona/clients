// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestInterpreterCreateContext_whenEnvSpecified_preservesEmptyMap(t *testing.T) {
	for _, tc := range []struct {
		name string
		env  map[string]string
		body string
	}{
		{"nil", nil, `{"kernel":"python"}`},
		{"empty", map[string]string{}, `{"kernel":"python","env":{}}`},
		{"populated", map[string]string{"MODE": "test"}, `{"kernel":"python","env":{"MODE":"test"}}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given an optional environment selected by the caller.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sessions": jsonHandler(t, map[string]string{"id": "context", "kernel": "python"}),
			})
			service := interpreterTestSandbox(t, fake).Interpreter()
			// When creating the context.
			_, err := service.CreateContext(t.Context(), "python", &CreateInterpreterContextOptions{Env: tc.env})
			// Then nil omits the field while an explicit empty map is sent.
			require.NoError(t, err)
			require.JSONEq(t, tc.body, string(fake.requests()[0].Body))
		})
	}
}
