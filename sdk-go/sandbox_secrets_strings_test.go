// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSandboxSecretsRejectsMalformedOptionalStrings(t *testing.T) {
	for _, field := range []string{"status", "error", "capture_status"} {
		for _, value := range []JSONValue{JSONValue(`12`), JSONValue(`false`), JSONValue(`[]`), JSONValue(`{}`)} {
			t.Run(fmt.Sprintf("%s/%s", field, value), func(t *testing.T) {
				// Given
				body := sandboxSecretsFixture()
				body[field] = value
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"GET /sandboxes/sbx/secrets": policyResponse(t, body),
				})
				sandbox := newMutatorSandbox(t, fake)
				// When
				_, err := sandbox.GetSecrets(t.Context())
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.Contains(t, err.Error(), field)
			})
		}
	}
}

func TestSandboxSecretsPreservesOptionalStrings(t *testing.T) {
	for _, value := range []JSONValue{nil, JSONValue(`null`), JSONValue(`""`), JSONValue(`"future-value"`)} {
		t.Run(string(value), func(t *testing.T) {
			// Given
			body := sandboxSecretsFixture()
			for _, field := range []string{"status", "error", "capture_status"} {
				delete(body, field)
				if value != nil {
					body[field] = value
				}
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/secrets": policyResponse(t, body),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			info, err := sandbox.GetSecrets(t.Context())
			// Then
			require.NoError(t, err)
			want := ""
			if string(value) == `"future-value"` {
				want = "future-value"
			}
			require.Equal(t, want, info.Status)
			require.Equal(t, want, info.Error)
			require.Equal(t, want, info.CaptureStatus)
		})
	}
}
