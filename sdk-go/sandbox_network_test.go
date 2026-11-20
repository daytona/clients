// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func networkPolicyFixture() map[string]any {
	return map[string]any{
		"desired_revision": 7, "applied_revision": 6, "applied_start_id": "boot-1", "status": "future-status",
		"desired": map[string]any{"version": 2, "block_all": true, "allow_cidrs": []string{}, "allow_domains": []string{"example.test"}},
		"applied": nil, "error": nil,
	}
}

func policyResponse(t *testing.T, body any) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("ETag", `"revision-7"`)
		writeJSON(t, w, body)
	}
}

func TestSandboxNetworkGet(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx/network-policy": policyResponse(t, networkPolicyFixture()),
	})
	sandbox := newMutatorSandbox(t, fake)
	// When
	info, err := sandbox.GetNetworkPolicy(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, `"revision-7"`, info.ETag)
	require.Equal(t, int64(7), info.DesiredRevision)
	require.Equal(t, int64(6), *info.AppliedRevision)
	require.Equal(t, "future-status", info.Status)
	require.Equal(t, "boot-1", info.AppliedStartID)
	require.Equal(t, []string{"example.test"}, info.Desired.AllowDomains)
	require.Empty(t, info.Desired.AllowCIDRs)
	require.Nil(t, info.Applied)
}

func TestSandboxNetworkUpdateShapes(t *testing.T) {
	on, off := true, false
	empty := []string{}
	var null []string
	for _, tc := range []struct {
		name string
		spec NetworkPolicySpec
		body string
	}{
		{"empty", NetworkPolicySpec{}, `{}`},
		{"false", NetworkPolicySpec{BlockAll: &off}, `{"block_all":false}`},
		{"empty cidrs", NetworkPolicySpec{AllowCIDRs: []string{}}, `{"allow_cidrs":[]}`},
		{"null domains", NetworkPolicySpec{AllowDomains: &null}, `{"allow_domains":null}`},
		{"empty domains", NetworkPolicySpec{AllowDomains: &empty}, `{"allow_domains":[]}`},
		{"cidrs and block", NetworkPolicySpec{BlockAll: &on, AllowCIDRs: []string{"10.0.0.0/8"}}, `{"block_all":true,"allow_cidrs":["10.0.0.0/8"]}`},
		{"cidrs and domains", NetworkPolicySpec{AllowDomains: &empty, AllowCIDRs: []string{"10.0.0.0/8"}}, `{"allow_domains":[],"allow_cidrs":["10.0.0.0/8"]}`},
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
			request := fake.requests()[0]
			require.JSONEq(t, tc.body, string(request.Body))
			require.Equal(t, `"np-6"`, request.Header.Get("If-Match"))
			require.Equal(t, "application/json", request.Header.Get("Content-Type"))
		})
	}
}

func TestSandboxNetworkResetSendsNull(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"PUT /sandboxes/sbx/network-policy": policyResponse(t, networkPolicyFixture()),
	})
	sandbox := newMutatorSandbox(t, fake)
	// When
	_, err := sandbox.ResetNetworkPolicy(t.Context(), &IfMatchOptions{IfMatch: `"np-6"`})
	// Then
	require.NoError(t, err)
	require.JSONEq(t, "null", string(fake.requests()[0].Body))
	require.Equal(t, `"np-6"`, fake.requests()[0].Header.Get("If-Match"))
}

func TestSandboxNetworkValidation(t *testing.T) {
	off := false
	empty := []string{}
	var null []string
	for name, spec := range map[string]NetworkPolicySpec{
		"open cidrs":         {AllowCIDRs: []string{"10.0.0.0/8"}},
		"false cidrs":        {BlockAll: &off, AllowCIDRs: []string{"10.0.0.0/8"}},
		"null domains cidrs": {AllowDomains: &null, AllowCIDRs: []string{"10.0.0.0/8"}},
		"domains false":      {BlockAll: &off, AllowDomains: &empty},
	} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.UpdateNetworkPolicy(t.Context(), spec, &IfMatchOptions{IfMatch: `"np-1"`})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			if name == "domains false" {
				require.ErrorIs(t, err, ErrNetworkPolicyDomainsRequireBlockAll)
			}
			require.Empty(t, fake.requests())
		})
	}
}

func TestSandboxNetworkRequiresValidIfMatch(t *testing.T) {
	for _, revision := range []string{"", " padded", "padded ", "line\r\n", "tab\t", "nonascii-é", "\x7f"} {
		t.Run(revision, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.ResetNetworkPolicy(t.Context(), &IfMatchOptions{IfMatch: revision})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}
