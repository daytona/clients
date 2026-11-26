// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func sandboxSecretsFixture() map[string]any {
	return map[string]any{
		"secrets": []string{"SERVICE_KEY"}, "desired_revision": 7, "applied_revision": 0,
		"status": "future-status", "error": "apply detail", "capture_status": "future-capture", "capture_unresolved_count": 0,
		"bindings": []map[string]any{{"name": "SERVICE_KEY", "env": "OPENAI_API_KEY"}},
	}
}

func TestSandboxSecretsGet(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/secrets": policyResponse(t, sandboxSecretsFixture()),
	})
	sandbox := newMutatorSandbox(t, fake)
	// When
	info, err := sandbox.GetSecrets(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, []string{"SERVICE_KEY"}, info.Secrets)
	require.Equal(t, `"revision-7"`, info.ETag)
	require.Equal(t, int64(7), info.DesiredRevision)
	require.Equal(t, int64(0), *info.AppliedRevision)
	require.Equal(t, int32(0), *info.CaptureUnresolvedCount)
	require.Equal(t, "future-status", info.Status)
	require.Equal(t, "future-capture", info.CaptureStatus)
	require.Equal(t, "apply detail", info.Error)
	require.Equal(t, []SandboxSecretBinding{{Name: "SERVICE_KEY", Env: "OPENAI_API_KEY"}}, info.Bindings)
}

func TestSandboxSecretsWithoutBindings(t *testing.T) {
	// Given
	body := sandboxSecretsFixture()
	delete(body, "bindings")
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx/secrets": policyResponse(t, body)})
	sandbox := newMutatorSandbox(t, fake)
	// When
	info, err := sandbox.GetSecrets(t.Context())
	// Then
	require.NoError(t, err)
	require.Nil(t, info.Bindings)
}

func TestSandboxSecretsReplace(t *testing.T) {
	for name, secrets := range map[string][]string{"names": {"SERVICE_KEY"}, "clear": nil} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"PUT /sandboxes/sbx/secrets": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.Header().Set("ETag", `"secrets-8"`)
					w.WriteHeader(http.StatusAccepted)
					writeJSON(t, w, sandboxSecretsFixture())
				},
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			info, err := sandbox.ReplaceSecrets(t.Context(), secrets, &IfMatchOptions{IfMatch: `"secrets-7"`})
			// Then
			require.NoError(t, err)
			require.Equal(t, `"secrets-8"`, info.ETag)
			require.Equal(t, `"secrets-7"`, fake.requests()[0].Header.Get("If-Match"))
			body := `{"secrets":["SERVICE_KEY"]}`
			if name == "clear" {
				body = `{"secrets":[]}`
			}
			require.JSONEq(t, body, string(fake.requests()[0].Body))
		})
	}
}

func TestSandboxSecretsRejectsMalformedFields(t *testing.T) {
	for field, invalid := range map[string][]any{
		"secrets":                  {nil, "name", []any{nil}, []any{true}},
		"desired_revision":         {nil, true, 1.5},
		"applied_revision":         {"1", 1.5},
		"capture_unresolved_count": {false, 1.5},
		"bindings":                 {"name", []any{"SERVICE_KEY"}, []any{map[string]any{"name": "SERVICE_KEY"}}, []any{map[string]any{"name": 1, "env": "E"}}},
	} {
		for index, value := range invalid {
			t.Run(field+"/"+string(rune('a'+index)), func(t *testing.T) {
				// Given
				body := sandboxSecretsFixture()
				body[field] = value
				fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx/secrets": policyResponse(t, body)})
				sandbox := newMutatorSandbox(t, fake)
				// When
				_, err := sandbox.GetSecrets(t.Context())
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
			})
		}
	}
}

func TestSandboxSecretsRequiresETag(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx/secrets": jsonHandler(t, sandboxSecretsFixture())})
	sandbox := newMutatorSandbox(t, fake)
	// When
	_, err := sandbox.GetSecrets(t.Context())
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
}

func TestSandboxSecretsRequiresIfMatch(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	sandbox := newMutatorSandbox(t, fake)
	// When
	_, err := sandbox.ReplaceSecrets(t.Context(), nil, nil)
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}
