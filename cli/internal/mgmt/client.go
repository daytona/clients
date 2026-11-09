// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package mgmt

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/google/uuid"
)

// GetMe calls GET /me.
func GetMe(ctx context.Context, c *api.Client) (*api.Raw[gen.User], error) {
	var me api.Raw[gen.User]
	if err := c.Do(ctx, http.MethodGet, "/me", nil, nil, &me); err != nil {
		return nil, err
	}
	return &me, nil
}

// OrgsPage reads one page of GET /orgs.
func OrgsPage(ctx context.Context, c *api.Client, cursor string, limit int64) (Page[gen.OrganizationListItem], error) {
	return getPage[gen.OrganizationListItem](ctx, c, "/orgs", "organizations", cursor, limit)
}

// ListOrgsRaw collects every page of GET /orgs, keeping the API's JSON.
func ListOrgsRaw(ctx context.Context, c *api.Client) ([]api.Raw[gen.OrganizationListItem], error) {
	var out []api.Raw[gen.OrganizationListItem]
	_, err := collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.OrganizationListItem, *string, error) {
		page, err := OrgsPage(ctx, c, cursor, 0)
		out = append(out, page.Items...)
		return api.Values(page.Items), &page.NextCursor, err
	})
	return out, err
}

// ListOrgs collects every page (name resolvers and pickers; the list commands read one page).
func ListOrgs(ctx context.Context, c *api.Client) ([]gen.OrganizationListItem, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.OrganizationListItem, *string, error) {
		page, err := OrgsPage(ctx, c, cursor, 0)
		return api.Values(page.Items), &page.NextCursor, err
	})
}

func GetOrg(ctx context.Context, c *api.Client, orgID string) (*api.Raw[gen.Organization], error) {
	var org api.Raw[gen.Organization]
	if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID), nil, nil, &org); err != nil {
		return nil, err
	}
	return &org, nil
}

// ProjectsPage reads one page of GET /orgs/{org_id}/projects.
func ProjectsPage(ctx context.Context, c *api.Client, orgID string, cursor string, limit int64) (Page[gen.Project], error) {
	return getPage[gen.Project](ctx, c, "/orgs/"+url.PathEscape(orgID)+"/projects", "projects", cursor, limit)
}

// ListProjects collects every page (name resolvers and pickers; the list commands read one page).
func ListProjects(ctx context.Context, c *api.Client, orgID string) ([]gen.Project, error) {
	return collectAll(ctx, func(ctx context.Context, cursor string) ([]gen.Project, *string, error) {
		page, err := ProjectsPage(ctx, c, orgID, cursor, 0)
		return api.Values(page.Items), &page.NextCursor, err
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

// Page is one server page of a management list: the items as the API sent
// them; NextCursor is empty on the last page.
type Page[T any] = api.Page[T]

// getPage reads one page of the items under field: `limit` 0 lets the
// server choose the page size.
func getPage[T any](ctx context.Context, c *api.Client, path, field, cursor string, limit int64) (Page[T], error) {
	query := url.Values{}
	if cursor != "" {
		query.Set("cursor", cursor)
	}
	if limit > 0 {
		query.Set("limit", strconv.FormatInt(limit, 10))
	}
	var body json.RawMessage
	if err := c.Do(ctx, http.MethodGet, path, query, nil, &body); err != nil {
		return Page[T]{}, err
	}
	page, err := api.DecodePage[T](body, field)
	if err != nil {
		return Page[T]{}, fmt.Errorf("decode GET %s response: %w", path, err)
	}
	return page, nil
}

// CreateKey calls POST /projects/{project_id}/api-keys.
func CreateKey(ctx context.Context, c *api.Client, projectID string, req gen.CreateProjectApiKeyRequest) (*api.Raw[gen.ProjectApiKeyWithToken], error) {
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
