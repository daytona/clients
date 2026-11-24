// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func sandboxAccessFixture() map[string]any {
	return map[string]any{
		"access_token": "delegated-" + "credential", "token_type": "Bearer", "expires_at": "2030-01-01T00:00:00Z",
		"operations": []string{"future:operation"}, "ports": []int{3000},
	}
}

func TestSandboxAccessTokenRequest(t *testing.T) {
	for _, tc := range []struct {
		name string
		opts *CreateAccessTokenOptions
		body string
	}{
		{"default", nil, `{}`},
		{"empty scopes", &CreateAccessTokenOptions{Operations: []string{}, Ports: []int{}}, `{"operations":[],"ports":[]}`},
		{"scoped", &CreateAccessTokenOptions{Operations: []string{"toolbox:fs"}, Ports: []int{3000}, TTL: 15 * time.Minute}, `{"operations":["toolbox:fs"],"ports":[3000],"ttl_seconds":900}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/sbx/access-token": jsonHandler(t, sandboxAccessFixture()),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			token, err := sandbox.CreateAccessToken(t.Context(), tc.opts)
			// Then
			require.NoError(t, err)
			require.Len(t, fake.requests(), 1)
			require.JSONEq(t, tc.body, string(fake.requests()[0].Body))
			require.Equal(t, "delegated-"+"credential", token.AccessToken)
			require.Equal(t, []string{"future:operation"}, token.Operations)
			require.Equal(t, []int{3000}, token.Ports)
			require.Equal(t, "Bearer", token.TokenType)
			require.Equal(t, 2030, token.ExpiresAt.Year())
			require.Equal(t, "caller-"+"credential", sandbox.auth.cache.snapshot().token)
			require.NotContains(t, fmt.Sprintf("%v %#v", token, token), token.AccessToken)
		})
	}
}

func TestSandboxAccessTokenValidation(t *testing.T) {
	for name, opts := range map[string]*CreateAccessTokenOptions{
		"zero port": {Ports: []int{0}}, "large port": {Ports: []int{65536}},
		"negative port": {Ports: []int{-1}}, "fraction ttl": {TTL: 1500 * time.Millisecond},
		"negative ttl": {TTL: -time.Second}, "sentinel ttl": {TTL: NoTimeout},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.CreateAccessToken(t.Context(), opts)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestSandboxAccessTokenRejectsMalformedFields(t *testing.T) {
	for field, invalid := range map[string][]any{
		"access_token": {nil, "", 1}, "token_type": {nil, false},
		"expires_at": {nil, "bad", "2030-01-01T00:00:00+24:00", 1},
		"operations": {nil, "toolbox", []any{nil}, []any{1}},
		"ports":      {"3000", []any{nil}, []any{true}, []any{1.5}},
	} {
		for index, value := range invalid {
			t.Run(field+"/"+string(rune('a'+index)), func(t *testing.T) {
				// Given
				body := sandboxAccessFixture()
				body[field] = value
				fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes/sbx/access-token": jsonHandler(t, body)})
				sandbox := newMutatorSandbox(t, fake)
				// When
				_, err := sandbox.CreateAccessToken(t.Context(), nil)
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
			})
		}
	}
}

func TestSandboxAccessTokenPreservesAbsentPorts(t *testing.T) {
	// Given
	body := sandboxAccessFixture()
	body["ports"] = nil
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes/sbx/access-token": jsonHandler(t, body)})
	sandbox := newMutatorSandbox(t, fake)
	// When
	token, err := sandbox.CreateAccessToken(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.Nil(t, token.Ports)
}

func TestSandboxAccessSigningRotation(t *testing.T) {
	for _, tc := range []struct {
		name      string
		direct    string
		wantError error
	}{
		{"API proxy", "", nil},
		{"scoped direct", "/toolbox/sbx", nil},
		{"template before suffix", "/{sandboxId}/toolbox", nil},
		{"snake template before suffix", "/{sandbox_id}/toolbox", nil},
		{"unscoped direct", "/toolbox", ErrUnsupportedOperation},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/sbx/signing-key/rotate": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
			})
			client := newTestClient(t, fake.Server)
			opts := GetSandboxOptions{}
			if tc.direct != "" {
				opts.ToolboxURL = fake.Server.URL + tc.direct
			}
			sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}, ToolboxURL: fake.Server.URL, Options: opts})
			require.NoError(t, err)
			// When
			err = sandbox.RotateSigningKey(t.Context())
			// Then
			require.ErrorIs(t, err, tc.wantError)
			if tc.wantError != nil {
				require.Empty(t, fake.requests())
			} else {
				require.Len(t, fake.requests(), 1)
				require.Empty(t, fake.requests()[0].Body)
			}
		})
	}
}
