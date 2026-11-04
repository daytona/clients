// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/daytona/clients/cli/internal/output"
)

func resolveMemberRef(items []gen.OrganizationMember, ref string) (*gen.OrganizationMember, error) {
	ref = strings.TrimSpace(ref)
	matchIndexes := mgmt.ResolveRefMatches(items, ref,
		func(item gen.OrganizationMember) string { return item.UserId.String() },
		func(item gen.OrganizationMember) []string {
			names := []string{item.Email}
			if item.Name != nil {
				names = append(names, *item.Name)
			}
			return names
		},
	)
	switch len(matchIndexes) {
	case 0:
		return nil, cerr.New(cerr.ExitNotFound, "no member %q", ref)
	case 1:
		return &items[matchIndexes[0]], nil
	default:
		ids := make([]string, 0, len(matchIndexes))
		for _, index := range matchIndexes {
			ids = append(ids, items[index].UserId.String())
		}
		return nil, cerr.Usage("member %q is ambiguous (%s)", ref, strings.Join(ids, ", ")).WithHint("pass the user id instead")
	}
}

func memberTable(items []gen.OrganizationMember) output.Table {
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		rows = append(rows, []string{item.UserId.String(), item.Email, formatPtr(item.Name), joinComma(item.Roles)})
	}
	return output.Table{Headers: []string{"USER_ID", "EMAIL", "NAME", "ROLES"}, Rows: rows}
}

func projectMemberTable(items []gen.ProjectMember) output.Table {
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		rows = append(rows, []string{item.UserId.String(), item.Email, formatPtr(item.Name), fmt.Sprintf("%t", item.Implicit), formatTimePtr(item.AddedAt)})
	}
	return output.Table{Headers: []string{"USER_ID", "EMAIL", "NAME", "IMPLICIT", "ADDED_AT"}, Rows: rows}
}
