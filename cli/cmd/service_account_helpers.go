// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/daytona/clients/cli/internal/output"
)

func resolveServiceAccountRef(items []gen.OrganizationServiceAccount, ref string) (*gen.OrganizationServiceAccount, error) {
	return resolveServiceAccountRefForOperation(items, ref, false)
}

func resolveServiceAccountRefForOperation(items []gen.OrganizationServiceAccount, ref string, includeArchived bool) (*gen.OrganizationServiceAccount, error) {
	ref = strings.TrimSpace(ref)
	matches := liveServiceAccounts(items)
	if includeArchived {
		matches = items
	}
	matchIndexes := mgmt.ResolveRefMatches(matches, ref,
		func(item gen.OrganizationServiceAccount) string { return item.Id.String() },
		func(item gen.OrganizationServiceAccount) []string { return []string{item.Name} },
	)
	switch len(matchIndexes) {
	case 0:
		return nil, cerr.New(cerr.ExitNotFound, "no service account %q", ref)
	case 1:
		return &matches[matchIndexes[0]], nil
	default:
		ids := make([]string, 0, len(matchIndexes))
		for _, index := range matchIndexes {
			ids = append(ids, matches[index].Id.String())
		}
		return nil, cerr.Usage("service account %q is ambiguous (%s)", ref, strings.Join(ids, ", ")).WithHint("pass the id instead")
	}
}

func liveServiceAccounts(items []gen.OrganizationServiceAccount) []gen.OrganizationServiceAccount {
	liveItems := make([]gen.OrganizationServiceAccount, 0, len(items))
	for i := range items {
		if items[i].ArchivedAt == nil {
			liveItems = append(liveItems, items[i])
		}
	}
	return liveItems
}

func serviceAccountTable(items []gen.OrganizationServiceAccount) output.Table {
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		rows = append(rows, []string{item.Id.String(), item.Name, joinComma(item.Roles), formatTimePtr(item.ArchivedAt), toString(item.LiveKeyCount)})
	}
	return output.Table{Headers: []string{"ID", "NAME", "ROLES", "ARCHIVED_AT", "LIVE_KEYS"}, Rows: rows}
}

func projectServiceAccountTable(items []gen.ProjectServiceAccount) output.Table {
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		rows = append(rows, []string{item.Id.String(), item.Name, toString(item.Implicit), formatTimePtr(item.AddedAt), formatTimePtr(item.ArchivedAt)})
	}
	return output.Table{Headers: []string{"ID", "NAME", "IMPLICIT", "ADDED_AT", "ARCHIVED_AT"}, Rows: rows}
}
