// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	openapi_types "github.com/oapi-codegen/runtime/types"
	"github.com/stretchr/testify/require"
)

func TestResolveMemberRef_exactCaseWins(t *testing.T) {
	items := []gen.OrganizationMember{
		{UserId: mustRefUUID(t, "11111111-1111-1111-1111-111111111111"), Email: "first@example.com", Name: stringPtrForTest("Acme")},
		{UserId: mustRefUUID(t, "22222222-2222-2222-2222-222222222222"), Email: "second@example.com", Name: stringPtrForTest("acme")},
	}
	match, err := resolveMemberRef(items, "Acme")
	require.NoError(t, err)
	require.Equal(t, "11111111-1111-1111-1111-111111111111", match.UserId.String())
}

func TestResolveServiceAccountRef_exactCaseWins(t *testing.T) {
	items := []gen.OrganizationServiceAccount{
		{Id: mustRefUUID(t, "11111111-1111-1111-1111-111111111111"), Name: "Acme"},
		{Id: mustRefUUID(t, "22222222-2222-2222-2222-222222222222"), Name: "acme"},
	}
	match, err := resolveServiceAccountRef(items, "Acme")
	require.NoError(t, err)
	require.Equal(t, "11111111-1111-1111-1111-111111111111", match.Id.String())
}

func mustRefUUID(t *testing.T, value string) openapi_types.UUID {
	t.Helper()
	var id openapi_types.UUID
	require.NoError(t, id.UnmarshalText([]byte(value)))
	return id
}
