// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
)

// maxOrgProjectNameLen is the API's limit on organization and project names,
// in characters after trimming.
const maxOrgProjectNameLen = 64

// checkOrgProjectName checks an organization or project name the way the API
// does on a create: 1 to 64 characters after trimming, no control characters,
// and not the reserved project name `default`.
func checkOrgProjectName(kind, name string) error {
	trimmed := strings.TrimSpace(name)
	switch {
	case trimmed == "":
		return cerr.Usage("the %s name must not be blank", kind)
	case strings.ContainsFunc(trimmed, unicode.IsControl):
		return cerr.Usage("the %s name must not contain control characters", kind)
	case kind == "project" && trimmed == "default":
		return cerr.Usage("the project name %q is reserved", trimmed)
	case utf8.RuneCountInString(trimmed) > maxOrgProjectNameLen:
		return cerr.Usage("the %s name must be at most %d characters (got %d)", kind, maxOrgProjectNameLen, utf8.RuneCountInString(trimmed))
	}
	return nil
}

// checkRenamedOrgName refuses an organization rename the API would refuse.
// The API accepts the current name sent back unchanged (after trimming)
// whatever it holds, so a refused name is looked up first, which costs one
// read. A caller that may not read it (403) is left to the API, which applies
// the same rule.
func checkRenamedOrgName(ctx context.Context, mc *mgmtClient, orgID, name string, refusal error) error {
	if refusal == nil {
		return nil
	}
	org, err := mgmt.GetOrg(ctx, mc, orgID)
	if cerr.ExitCode(err) == cerr.ExitForbidden {
		return nil
	}
	if err != nil {
		return err
	}
	if org.V.Name != "" && org.V.Name == strings.TrimSpace(name) {
		return nil
	}
	return refusal
}

// checkRenamedProjectName is checkRenamedOrgName for a project; current is
// the project's name when its reference already read it, else "". A project
// the selected organization's list does not show is left to the API.
func checkRenamedProjectName(ctx context.Context, mc *mgmtClient, orgID, projectID, current, name string, refusal error) error {
	if refusal == nil {
		return nil
	}
	if current == "" {
		projects, err := mgmt.ListProjects(ctx, mc, orgID)
		if cerr.ExitCode(err) == cerr.ExitForbidden {
			return nil
		}
		if err != nil {
			return err
		}
		found := false
		for _, p := range projects {
			if p.Id.String() == projectID {
				current, found = p.Name, true
				break
			}
		}
		if !found {
			return nil
		}
	}
	if current != "" && current == strings.TrimSpace(name) {
		return nil
	}
	return refusal
}
