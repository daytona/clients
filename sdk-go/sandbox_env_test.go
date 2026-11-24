// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSandboxEnvWire(t *testing.T) {
	for _, tc := range []struct {
		name string
		env  map[string]string
		opts *UpdateEnvOptions
		body string
	}{
		{"defaults", nil, nil, `{"env":{},"unset":[]}`},
		{"valid", map[string]string{"GREETING": "hello\nworld", "ODD KEY": "allowed"}, &UpdateEnvOptions{Unset: []string{"OLD"}}, `{"env":{"GREETING":"hello\nworld","ODD KEY":"allowed"},"unset":["OLD"]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /toolbox/sbx/env": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			err := sandbox.UpdateEnv(t.Context(), tc.env, tc.opts)
			// Then
			require.NoError(t, err)
			require.Len(t, fake.requests(), 1)
			require.JSONEq(t, tc.body, string(fake.requests()[0].Body))
		})
	}
}

func TestSandboxEnvInvalidNames(t *testing.T) {
	for _, key := range []string{"", "BAD=KEY", "BAD\x00KEY", "BAD\nKEY", "BAD\rKEY"} {
		for _, unset := range []bool{false, true} {
			t.Run(key+map[bool]string{true: "/unset", false: "/set"}[unset], func(t *testing.T) {
				// Given
				fake := newFakeServer(t, nil)
				sandbox := newMutatorSandbox(t, fake)
				env := map[string]string{key: "value"}
				var opts *UpdateEnvOptions
				if unset {
					env, opts = nil, &UpdateEnvOptions{Unset: []string{key}}
				}
				// When
				err := sandbox.UpdateEnv(t.Context(), env, opts)
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.Empty(t, fake.requests())
			})
		}
	}
}

func TestSandboxEnvRejectsNULValue(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	sandbox := newMutatorSandbox(t, fake)
	// When
	err := sandbox.UpdateEnv(t.Context(), map[string]string{"NAME": "value\x00"}, nil)
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}
