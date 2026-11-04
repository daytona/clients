// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package mgmt

import (
	"context"
	"net/http"
	"net/url"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/google/uuid"
)

// GetMe calls GET /me.
func GetMe(ctx context.Context, c *api.Client) (*gen.User, error) {
	var me gen.User
	if err := c.Do(ctx, http.MethodGet, "/me", nil, nil, &me); err != nil {
		return nil, err
	}
	return &me, nil
}

// ListOrgs calls GET /orgs.
func ListOrgs(ctx context.Context, c *api.Client) ([]gen.OrganizationListItem, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.OrganizationListItem, *string, error) {
		var resp gen.OrganizationPage
		if err := c.Do(ctx, http.MethodGet, "/orgs", cursorQuery(cursor), nil, &resp); err != nil {
			return nil, nil, err
		}
		return resp.Organizations, resp.NextCursor, nil
	})
}

func GetOrg(ctx context.Context, c *api.Client, orgID string) (*gen.Organization, error) {
	var org gen.Organization
	if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID), nil, nil, &org); err != nil {
		return nil, err
	}
	return &org, nil
}

// ListProjects calls GET /orgs/{org_id}/projects.
func ListProjects(ctx context.Context, c *api.Client, orgID string) ([]gen.Project, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.Project, *string, error) {
		var resp gen.ProjectPage
		if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID)+"/projects", cursorQuery(cursor), nil, &resp); err != nil {
			return nil, nil, err
		}
		return resp.Projects, resp.NextCursor, nil
	})
}

func collectAll[T any](ctx context.Context, fetch func(context.Context, string) ([]T, *string, error)) ([]T, error) {
	items := []T{}
	seen := map[string]struct{}{}
	cursor := ""
	for {
		page, next, err := fetch(ctx, cursor)
		if err != nil {
			return nil, err
		}
		items = append(items, page...)
		if next == nil || *next == "" {
			return items, nil
		}
		if _, ok := seen[*next]; ok {
			return nil, cerr.New(cerr.ExitFailure, "server repeated pagination cursor %q", *next)
		}
		seen[*next] = struct{}{}
		cursor = *next
	}
}

func cursorQuery(cursor string) url.Values {
	if cursor == "" {
		return nil
	}
	return url.Values{"cursor": {cursor}}
}

// CreateKey calls POST /projects/{project_id}/api-keys.
func CreateKey(ctx context.Context, c *api.Client, projectID string, req gen.CreateProjectApiKeyRequest) (*gen.ProjectApiKeyWithToken, error) {
	return CreateProjectKey(ctx, c, projectID, req)
}

// ResolveOrg accepts an id or an unambiguous name.
func ResolveOrg(orgs []gen.OrganizationListItem, ref string) (*gen.OrganizationListItem, error) {
	ref = strings.TrimSpace(ref)
	for i := range orgs {
		if sameUUIDRef(orgs[i].Id.String(), ref) {
			return &orgs[i], nil
		}
	}
	matches := nameMatches(ref, len(orgs), func(i int) string { return orgs[i].Name })
	switch len(matches) {
	case 1:
		return &orgs[matches[0]], nil
	case 0:
		return nil, cerr.New(cerr.ExitNotFound, "no organization %q", ref).WithHint("see `daytona org list`")
	}
	ids := make([]string, 0, len(matches))
	for _, i := range matches {
		ids = append(ids, orgs[i].Id.String())
	}
	return nil, cerr.Usage("organization name %q is ambiguous (%s)", ref, strings.Join(ids, ", ")).WithHint("pass the id instead")
}

// ResolveProject accepts an id or an unambiguous name within one org.
func ResolveProject(projects []gen.Project, ref string) (*gen.Project, error) {
	ref = strings.TrimSpace(ref)
	for i := range projects {
		if sameUUIDRef(projects[i].Id.String(), ref) {
			return &projects[i], nil
		}
	}
	matches := nameMatches(ref, len(projects), func(i int) string { return projects[i].Name })
	switch len(matches) {
	case 1:
		return &projects[matches[0]], nil
	case 0:
		return nil, cerr.New(cerr.ExitNotFound, "no project %q in this organization", ref).WithHint("see `daytona project list`")
	}
	ids := make([]string, 0, len(matches))
	for _, i := range matches {
		ids = append(ids, projects[i].Id.String())
	}
	return nil, cerr.Usage("project name %q is ambiguous (%s)", ref, strings.Join(ids, ", ")).WithHint("pass the id instead")
}

// ResolveRefMatches applies the CLI's shared ref-resolution rules: UUID match
// first, then one exact (case-sensitive) name match, else all case-folded
// matches for the caller to treat as one match, ambiguous, or missing.
func ResolveRefMatches[T any](items []T, ref string, id func(T) string, names func(T) []string) []int {
	ref = strings.TrimSpace(ref)
	for i := range items {
		if sameUUIDRef(id(items[i]), ref) {
			return []int{i}
		}
	}
	var exact, folded []int
	for i := range items {
		exactMatch := false
		foldedMatch := false
		for _, candidate := range names(items[i]) {
			switch {
			case candidate == ref:
				exactMatch = true
			case strings.EqualFold(candidate, ref):
				foldedMatch = true
			}
		}
		if exactMatch {
			exact = append(exact, i)
			continue
		}
		if foldedMatch {
			folded = append(folded, i)
		}
	}
	if len(exact) == 1 {
		return exact
	}
	return append(exact, folded...)
}

// DefaultProject returns the org's `default` project, else the first.
func DefaultProject(projects []gen.Project) *gen.Project {
	for i := range projects {
		if projects[i].Default {
			return &projects[i]
		}
	}
	for i := range projects {
		if projects[i].Name == DefaultProjectName {
			return &projects[i]
		}
	}
	if len(projects) > 0 {
		return &projects[0]
	}
	return nil
}

// nameMatches resolves a name reference against n candidates: an exact
// (case-sensitive) match wins outright; otherwise case-insensitive matches are
// returned and the caller decides whether one is unambiguous. `gh` and
// `kubectl` behave the same way — "Acme" picks Acme even when "acme" exists,
// but "acme" with both present is ambiguous.
func nameMatches(ref string, n int, name func(int) string) []int {
	var exact, folded []int
	for i := 0; i < n; i++ {
		switch {
		case name(i) == ref:
			exact = append(exact, i)
		case strings.EqualFold(name(i), ref):
			folded = append(folded, i)
		}
	}
	if len(exact) == 1 {
		return exact
	}
	return append(exact, folded...)
}

func sameUUIDRef(candidate, ref string) bool {
	parsedCandidate, candidateErr := uuid.Parse(candidate)
	parsedRef, refErr := uuid.Parse(ref)
	if candidateErr == nil && refErr == nil {
		return parsedCandidate == parsedRef
	}
	return strings.EqualFold(candidate, ref)
}
