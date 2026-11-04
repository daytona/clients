// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestOrgGet_UUIDRefSkipsOrgList(t *testing.T) {
	api := newFakeAPI(t)
	api.on(http.MethodGet, "/orgs/"+testOrgP, http.StatusOK, organizationFixture(testOrgP, "Personal", true))
	stdout, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "dtno_orgkey", "org", "get", strings.ToUpper(testOrgP), "-o", "json")
	require.Equal(t, 0, code, stderr)
	require.Equal(t, testOrgP, mustJSON(t, stdout)["id"])
	require.Len(t, api.requests, 1)
	require.Equal(t, "/orgs/"+testOrgP, api.requests[0].Path)
}
