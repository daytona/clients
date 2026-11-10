// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"testing"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestDeviceLogin_emitsEventBeforeApprovalWhenMachineReadable(t *testing.T) {
	for _, format := range []string{"json", "yaml", "table"} {
		t.Run(format, func(t *testing.T) {
			// Given: approval fails, so a post-approval event would never be emitted.
			f := newFakeAPI(t)
			f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
			f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{
				"authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device",
			})
			f.on("POST", "/idp/device", 200, map[string]any{
				"device_code": "private-device-code", "user_code": "ABCD", "verification_uri": "https://idp.example/device", "verification_uri_complete": "https://idp.example/device?user_code=ABCD", "expires_in": 60,
			})
			f.on("POST", "/idp/token", 400, map[string]string{"error": "access_denied"})
			// When
			stdout, _, code := run(t, cfgDir(t), nil, "login", "--device", "--api-url", f.srv.URL, "-o", format)
			// Then: YAML mode also emits a JSON event, and never the device credential.
			require.Equal(t, cerr.ExitAuth, code)
			require.JSONEq(t, `{"verification_uri":"https://idp.example/device","verification_uri_complete":"https://idp.example/device?user_code=ABCD","user_code":"ABCD","expires_in":60}`, stdout)
		})
	}
}

func TestDeviceLogin_omitsUnknownFieldsWhenApprovalSucceeds(t *testing.T) {
	// Given
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{
		"authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device",
	})
	f.on("POST", "/idp/device", 200, map[string]any{"device_code": "dc", "user_code": "ABCD", "verification_uri": "https://idp.example/device", "expires_in": 60})
	f.on("POST", "/idp/token", 200, map[string]any{"access_token": "jwt", "refresh_token": "rt", "expires_in": 3600})
	f.on("GET", "/me", 200, map[string]any{"id": testUserID, "email": "me@x"})
	f.on("GET", "/orgs", 200, organizationsFixture())
	f.on("GET", "/orgs/"+testOrgP+"/projects", 200, projectsFixture(testOrgP, "default"))
	// When
	stdout, stderr, code := run(t, cfgDir(t), nil, "login", "--device", "--api-url", f.srv.URL, "-o", "json")
	// Then: one event is the entire stdout, even on success.
	require.Zero(t, code, stderr)
	require.JSONEq(t, `{"verification_uri":"https://idp.example/device","user_code":"ABCD","expires_in":60}`, stdout)
}

func TestDeviceLogin_keepsStdoutEmptyWhenTableAtTerminal(t *testing.T) {
	// Given
	tty := true
	previous := app.ForceTTY
	app.ForceTTY = &tty
	t.Cleanup(func() { app.ForceTTY = previous })
	f := newFakeAPI(t)
	f.on("GET", "/config", 200, oidcConfigFixture(f.srv.URL))
	f.on("GET", "/idp/.well-known/openid-configuration", 200, map[string]string{
		"authorization_endpoint": f.srv.URL + "/idp/authorize", "token_endpoint": f.srv.URL + "/idp/token", "device_authorization_endpoint": f.srv.URL + "/idp/device",
	})
	f.on("POST", "/idp/device", 200, map[string]any{"device_code": "dc", "user_code": "ABCD", "verification_uri": "https://idp.example/device", "expires_in": 60})
	f.on("POST", "/idp/token", 400, map[string]string{"error": "access_denied"})
	// When
	stdout, stderr, code := run(t, cfgDir(t), nil, "login", "--device", "--api-url", f.srv.URL, "-o", "table")
	// Then
	require.Equal(t, cerr.ExitAuth, code)
	require.Empty(t, stdout)
	require.Contains(t, stderr, "ABCD")
}
