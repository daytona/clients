// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestNetworkPolicyUpstreamProxyWire(t *testing.T) {
	on := true
	domains := []string{"pypi.org"}
	for _, tc := range []struct {
		name string
		spec NetworkPolicySpec
		body string
	}{
		{"omitted", NetworkPolicySpec{}, `{}`},
		{
			"no credentials",
			NetworkPolicySpec{UpstreamProxy: &UpstreamProxySpec{URL: "http://proxy.example.com:3128"}},
			`{"upstream_proxy":{"url":"http://proxy.example.com:3128"}}`,
		},
		{
			"credentials",
			NetworkPolicySpec{UpstreamProxy: &UpstreamProxySpec{URL: "https://proxy.example.com", CredentialsSecret: "PROXY_AUTH"}},
			`{"upstream_proxy":{"url":"https://proxy.example.com","credentials_secret":"PROXY_AUTH"}}`,
		},
		{
			"with rules",
			NetworkPolicySpec{BlockAll: &on, AllowDomains: &domains, UpstreamProxy: &UpstreamProxySpec{URL: "http://proxy.example.com"}},
			`{"block_all":true,"allow_domains":["pypi.org"],"upstream_proxy":{"url":"http://proxy.example.com"}}`,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"PUT /sandboxes/sbx/network-policy": policyResponse(t, networkPolicyFixture()),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.UpdateNetworkPolicy(t.Context(), tc.spec, &IfMatchOptions{IfMatch: `"np-6"`})
			// Then
			require.NoError(t, err)
			require.Len(t, fake.requests(), 1)
			require.JSONEq(t, tc.body, string(fake.requests()[0].Body))
		})
	}
}

func TestCreate_sendsUpstreamProxy(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes": jsonHandler(t, map[string]any{"id": "sbx", "status": "running"}),
	})
	client := newTestClient(t, fake.Server)

	// When
	_, err := client.Create(t.Context(), "", &CreateOptions{
		IdempotencyKey: "proxy", Wait: NoWait,
		NetworkPolicy: &NetworkPolicySpec{UpstreamProxy: &UpstreamProxySpec{URL: "https://proxy.example.com", CredentialsSecret: "PROXY_AUTH"}},
	})

	// Then
	require.NoError(t, err)
	require.JSONEq(t, `{"idempotency_key":"proxy","wait_seconds":0,
		"network_policy":{"upstream_proxy":{"url":"https://proxy.example.com","credentials_secret":"PROXY_AUTH"}}}`,
		string(fake.requests()[0].Body))
}

func TestNetworkPolicyReadsUpstreamProxy(t *testing.T) {
	// Given
	body := networkPolicyFixture()
	body["desired"] = map[string]any{
		"version": 3, "block_all": false, "allow_cidrs": []string{},
		"upstream_proxy": map[string]any{"url": "https://proxy.example.com:443", "credentials_secret": "PROXY_AUTH"},
	}
	body["applied"] = map[string]any{
		"version": 3, "block_all": false, "allow_cidrs": []string{},
		"upstream_proxy": map[string]any{"url": "http://proxy.example.com:80", "credentials_secret": nil},
	}
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/network-policy": policyResponse(t, body),
	})
	sandbox := newMutatorSandbox(t, fake)
	// When
	info, err := sandbox.GetNetworkPolicy(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, int64(3), info.Desired.Version)
	require.Equal(t, &UpstreamProxy{URL: "https://proxy.example.com:443", CredentialsSecret: "PROXY_AUTH"}, info.Desired.UpstreamProxy)
	require.Equal(t, &UpstreamProxy{URL: "http://proxy.example.com:80"}, info.Applied.UpstreamProxy)
}

func TestNetworkPolicyWithoutUpstreamProxy(t *testing.T) {
	for _, value := range []any{nil, JSONValue(`null`)} {
		// Given
		body := networkPolicyFixture()
		desired, ok := body["desired"].(map[string]any)
		require.True(t, ok)
		if value != nil {
			desired["upstream_proxy"] = value
		}
		fake := newFakeServer(t, map[string]http.HandlerFunc{
			"GET /sandboxes/sbx/network-policy": policyResponse(t, body),
		})
		sandbox := newMutatorSandbox(t, fake)
		// When
		info, err := sandbox.GetNetworkPolicy(t.Context())
		// Then
		require.NoError(t, err)
		require.Nil(t, info.Desired.UpstreamProxy)
	}
}

func TestNetworkPolicyRejectsMalformedUpstreamProxy(t *testing.T) {
	for name, value := range map[string]any{
		"not an object":      "http://proxy.example.com:80",
		"missing url":        map[string]any{},
		"url type":           map[string]any{"url": 1},
		"credentials type":   map[string]any{"url": "http://proxy.example.com:80", "credentials_secret": 7},
		"credentials object": map[string]any{"url": "http://proxy.example.com:80", "credentials_secret": map[string]any{}},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			body := networkPolicyFixture()
			desired, ok := body["desired"].(map[string]any)
			require.True(t, ok)
			desired["upstream_proxy"] = value
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/network-policy": policyResponse(t, body),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.GetNetworkPolicy(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Contains(t, err.Error(), "desired.upstream_proxy")
		})
	}
}
