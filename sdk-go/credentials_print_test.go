// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestPrintedValues_neverContainCredentials(t *testing.T) {
	// Given a client with an API key and a sandbox holding a minted token
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/access-token": jsonHandler(t, map[string]any{
			"access_token": "sandbox-token-secret", "expires_at": "2040-01-01T00:00:00Z",
			"token_type": "Bearer", "operations": []string{"toolbox"},
		}),
	})
	client := newTestClient(t, fake.Server, WithAPIKey("api-key-secret"))
	sandbox, err := client.newSandbox(sandboxInit{
		Info: SandboxInfo{ID: "sbx", Name: "named"}, ToolboxURL: fake.Server.URL + "/toolbox",
		AccessToken: "create-token-secret", AccessTokenExpiresAt: time.Now().Add(time.Hour), FromCreate: true,
	})
	require.NoError(t, err)
	_, _, err = sandbox.toolbox().connectionInfo(t.Context())
	require.NoError(t, err)
	failure := &Error{Message: "denied", Code: "UNAUTHORIZED", StatusCode: 401}

	// When every SDK value is printed with each verb
	var printed strings.Builder
	for _, value := range []any{client, sandbox, sandbox.Info(), failure} {
		for _, verb := range []string{"%v", "%+v", "%#v", "%s"} {
			printed.WriteString(fmt.Sprintf(verb, value) + "\n")
		}
	}

	// Then no credential appears
	for _, secret := range []string{"api-key-secret", "sandbox-token-secret", "create-token-secret"} {
		require.NotContains(t, printed.String(), secret)
	}
}
