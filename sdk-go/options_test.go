// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestNew_whenCredentialConfigurationVaries(t *testing.T) {
	cases := []struct {
		name              string
		key, jwt, project string
		options           []Option
		wantKey, wantJWT  string
		want              error
	}{
		{name: "missing", want: ErrAuthentication},
		{name: "ambient whitespace", key: " \t\n ", want: ErrAuthentication},
		{name: "trim ambient", key: " ambient \n", wantKey: "ambient"},
		{name: "explicit key wins", jwt: "environment-jwt", options: []Option{WithAPIKey("explicit")}, wantKey: "explicit"},
		{name: "explicit jwt wins", key: "ambient", options: []Option{WithJWTToken("jwt"), WithProjectID("p")}, wantJWT: "jwt"},
		{name: "ambient collision", key: "key", jwt: "jwt", project: "p", want: ErrBadRequest},
		{name: "explicit collision", options: []Option{WithAPIKey("key"), WithJWTToken("jwt")}, want: ErrBadRequest},
		{name: "empty key", key: "ambient", options: []Option{WithAPIKey("")}, want: ErrBadRequest},
		{name: "empty jwt", options: []Option{WithJWTToken("")}, want: ErrBadRequest},
		{name: "empty project", key: "key", project: "p", options: []Option{WithProjectID("")}, want: ErrBadRequest},
		{name: "jwt missing project", jwt: "jwt", want: ErrBadRequest},
		{name: "explicit padded", options: []Option{WithAPIKey(" key\t\r\n")}, wantKey: "key"},
		{name: "explicit padded jwt", options: []Option{WithJWTToken("jwt\n"), WithProjectID("p")}, wantJWT: "jwt"},
		{name: "explicit internal space", options: []Option{WithAPIKey("k ey")}, wantKey: "k ey"},
		{name: "explicit blank", key: "ambient", options: []Option{WithAPIKey(" \t\n")}, want: ErrBadRequest},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			// Given
			t.Setenv("DAYTONA_API_KEY", test.key)
			t.Setenv("DAYTONA_JWT_TOKEN", test.jwt)
			t.Setenv("DAYTONA_PROJECT_ID", test.project)
			t.Setenv("DAYTONA_API_URL", "")
			// When
			client, err := New(test.options...)
			// Then
			if test.want != nil {
				require.ErrorIs(t, err, test.want)
				var detail *Error
				require.ErrorAs(t, err, &detail)
				require.Equal(t, SourceSDK, detail.Source)
				return
			}
			require.NoError(t, err)
			defer func() { require.NoError(t, client.Close()) }()
			if test.wantKey != "" {
				require.Equal(t, test.wantKey, *client.config.apiKey)
			}
			if test.wantJWT != "" {
				require.Equal(t, test.wantJWT, *client.config.jwtToken)
			}
		})
	}
}

func TestNew_whenCredentialIsMalformed(t *testing.T) {
	const secret = "dtn_config_secret"
	cases := []struct{ value, suffix string }{
		{" \t\n", " must not be blank"},
		{secret + "\u00e9", " must be printable ASCII with no leading or trailing whitespace"},
		{secret + "\x00x", " must be printable ASCII with no leading or trailing whitespace"},
		{"dtn_\x01" + secret, " must be printable ASCII with no leading or trailing whitespace"},
		{secret + "\r\nX-Injected: 1", " must be printable ASCII with no leading or trailing whitespace"},
	}
	for _, test := range cases {
		for _, option := range []struct {
			name   string
			option Option
		}{{"WithAPIKey", WithAPIKey(test.value)}, {"WithJWTToken", WithJWTToken(test.value)}} {
			t.Run(option.name+"/"+test.value, func(t *testing.T) {
				// Given
				t.Setenv("DAYTONA_API_KEY", "")
				t.Setenv("DAYTONA_JWT_TOKEN", "")
				// When
				_, err := New(option.option, WithProjectID("project"))
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				var detail *Error
				require.ErrorAs(t, err, &detail)
				require.Equal(t, option.name+test.suffix, detail.Message)
				require.Equal(t, SourceSDK, detail.Source)
				require.NotContains(t, fmt.Sprintf("%+v %#v", err, detail), secret)
			})
		}
	}
	for _, variable := range []string{"DAYTONA_API_KEY", "DAYTONA_JWT_TOKEN"} {
		t.Run(variable, func(t *testing.T) {
			// Given: trimmed, then still not a header value
			t.Setenv("DAYTONA_API_KEY", "")
			t.Setenv("DAYTONA_JWT_TOKEN", "")
			t.Setenv(variable, "  "+secret+"\r\nX-Injected: 1\n")
			// When
			_, err := New(WithProjectID("project"))
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Contains(t, err.Error(), variable+" must be printable ASCII")
			require.NotContains(t, err.Error(), secret)
		})
	}
}

func TestNew_sendsAPaddedExplicitKeyTrimmed(t *testing.T) {
	// Given: a key read from a file, with its trailing newline
	var authorization string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authorization = r.Header.Get("Authorization")
		writeJSON(t, w, map[string]any{"targets": []any{}})
	}))
	defer server.Close()
	client := newTestClient(t, server, WithAPIKey(" dtn_file_key\n"))
	// When
	_, err := client.ListAuditActions(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, "Bearer dtn_file_key", authorization)
}

func TestNew_whenEndpointEnvironmentVaries(t *testing.T) {
	for _, test := range []struct{ input, expected string }{
		{"", DefaultAPIURL}, {"  //  ", DefaultAPIURL}, {" https://api.example/base/// \n", "https://api.example/base"},
	} {
		t.Run(test.input, func(t *testing.T) {
			// Given
			t.Setenv("DAYTONA_V2_API_URL", test.input)
			t.Setenv("DAYTONA_API_URL", "https://app.daytona.io/api")
			// When
			client, err := New(WithAPIKey("key"))
			// Then
			require.NoError(t, err)
			defer func() { require.NoError(t, client.Close()) }()
			require.Equal(t, test.expected, client.config.baseURL)
		})
	}
}

func TestNew_whenOptionsAreInvalid(t *testing.T) {
	for _, option := range []Option{
		nil, WithRequestTimeout(-10), WithMaxResponseBytes(-2), WithMaxResponseBytes(maxSafeInteger + 1),
		WithHTTPClient(nil), WithLogger(nil), WithSandboxEvents(SandboxEventsConfig{ReconcileInterval: -time.Second}),
		WithSandboxEvents(SandboxEventsConfig{IdleClose: NoWait}),
	} {
		// Given/When
		client, err := New(WithAPIKey("key"), option)
		// Then
		require.Nil(t, client)
		require.ErrorIs(t, err, ErrBadRequest)
	}
}

func TestClient_whenClosed(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /sandboxes/sbx": jsonHandler(t, sandboxFixture("sbx"))})
	client := newTestClient(t, fake.Server)
	sandbox, err := client.Get(t.Context(), "sbx", nil)
	require.NoError(t, err)
	require.NoError(t, client.Close())
	// When/Then
	require.NoError(t, client.Close())
	_, err = client.Get(t.Context(), ".", nil)
	require.ErrorIs(t, err, ErrClientClosed)
	require.ErrorIs(t, sandbox.Refresh(t.Context()), ErrClientClosed)
	require.ErrorIs(t, sandbox.toolbox().do(t.Context(), toolboxRequest{Path: "/files"}), ErrClientClosed)
	require.Len(t, fake.requests(), 1)
}
