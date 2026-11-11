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
// does: 1 to 64 characters after trimming, no control characters, and not
// the reserved project name `default`. A name refused only for its length is
// returned as tooLong instead of err: a rename may still send it when it is
// the current name.
func checkOrgProjectName(kind, name string) (tooLong, err error) {
	trimmed := strings.TrimSpace(name)
	switch {
	case trimmed == "":
		return nil, cerr.Usage("the %s name must not be blank", kind)
	case strings.ContainsFunc(trimmed, unicode.IsControl):
		return nil, cerr.Usage("the %s name must not contain control characters", kind)
	case kind == "project" && trimmed == "default":
		return nil, cerr.Usage("the project name %q is reserved", trimmed)
	case utf8.RuneCountInString(trimmed) > maxOrgProjectNameLen:
		return cerr.Usage("the %s name must be at most %d characters (got %d)", kind, maxOrgProjectNameLen, utf8.RuneCountInString(trimmed)), nil
	}
	return nil, nil
}

// checkNewOrgProjectName is checkOrgProjectName for a create, where every
// refusal applies.
func checkNewOrgProjectName(kind, name string) error {
	tooLong, err := checkOrgProjectName(kind, name)
	if err != nil {
		return err
	}
	return tooLong
}

// checkRenamedOrgName refuses an organization rename the API would refuse. A
// name over the length limit is accepted when it is the organization's
// current name, which costs one read.
func checkRenamedOrgName(ctx context.Context, mc *mgmtClient, orgID, name string, tooLong error) error {
	if tooLong == nil {
		return nil
	}
	org, err := mgmt.GetOrg(ctx, mc, orgID)
	if err != nil {
		return err
	}
	if org.V.Name == strings.TrimSpace(name) {
		return nil
	}
	return tooLong
}

// checkRenamedProjectName is checkRenamedOrgName for a project; current is
// the project's name when its reference already read it, else "".
func checkRenamedProjectName(ctx context.Context, mc *mgmtClient, orgID, projectID, current, name string, tooLong error) error {
	if tooLong == nil {
		return nil
	}
	if current == "" {
		projects, err := mgmt.ListProjects(ctx, mc, orgID)
		if err != nil {
			return err
		}
		for _, p := range projects {
			if p.Id.String() == projectID {
				current = p.Name
				break
			}
		}
	}
	if current == strings.TrimSpace(name) {
		return nil
	}
	return tooLong
}
