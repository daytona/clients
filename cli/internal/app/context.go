// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package app

import (
	"context"
	"strings"

	"github.com/daytona/clients/cli/internal/cerr"
)

// OrgFromOverride reports whether Profile.OrgID came from --org / DAYTONA_ORG
// rather than from the saved profile.
func (a *App) OrgFromOverride() bool { return a.orgFromOverride }

// ProjectFromOverride reports whether the selected project came from
// --project / DAYTONA_PROJECT rather than from the saved profile.
func (a *App) ProjectFromOverride() bool { return a.projectFromOverride }

// RequireOrg returns the selected org id or a usage error / picker.
func (a *App) RequireOrg(ctx context.Context, pick func(ctx context.Context) (string, error)) (string, error) {
	if a.Profile.OrgID != "" {
		if err := a.resolveSelectedOrg(ctx); err != nil {
			return "", err
		}
		return a.Profile.OrgID, nil
	}
	if a.Mode.Interactive && pick != nil {
		return pick(ctx)
	}
	return "", cerr.MissingArg("an organization", "--org (or `daytona org use`)")
}

func looksLikeUUID(s string) bool {
	if len(s) != 36 {
		return false
	}
	for i, ch := range s {
		switch i {
		case 8, 13, 18, 23:
			if ch != '-' {
				return false
			}
		default:
			if !((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') || (ch >= 'A' && ch <= 'F')) {
				return false
			}
		}
	}
	return true
}

// RequireProject returns the selected project id or a usage error / picker.
func (a *App) RequireProject(ctx context.Context, pick func(ctx context.Context) (string, error)) (string, error) {
	if project, err := a.resolveSelectedProject(ctx); err != nil {
		return "", err
	} else if project != "" {
		return project, nil
	}
	if a.Mode.Interactive && pick != nil {
		return pick(ctx)
	}
	return "", cerr.MissingArg("a project", "--project (or `daytona project use`)")
}

func (a *App) resolveSelectedOrg(ctx context.Context) error {
	if a.Profile == nil || a.Profile.OrgID == "" || a.orgIDResolved || !a.orgFromOverride || a.OrgResolver == nil || looksLikeUUID(a.Profile.OrgID) {
		return nil
	}
	raw := a.Profile.OrgID
	id, err := a.OrgResolver(ctx, raw)
	if err != nil {
		return err
	}
	if id != raw && a.Profile.ProjectByOrg != nil {
		if project, ok := a.Profile.ProjectByOrg[raw]; ok {
			delete(a.Profile.ProjectByOrg, raw)
			a.Profile.ProjectByOrg[id] = project
		}
	}
	a.Profile.OrgID = id
	a.orgFromOverride = false
	a.orgIDResolved = true
	return nil
}

func (a *App) resolveSelectedProject(ctx context.Context) (string, error) {
	if a.Profile == nil {
		return "", nil
	}
	if err := a.resolveSelectedOrg(ctx); err != nil {
		return "", err
	}
	project := strings.TrimSpace(a.Profile.ProjectID())
	if project == "" {
		return project, nil
	}
	if looksLikeUUID(project) {
		// One spelling for the caches keyed by it (workload keys, platform
		// tokens): `ABC…` and `abc…` are the same project.
		return strings.ToLower(project), nil
	}
	if a.ProjectResolver == nil {
		return project, nil
	}
	// A name — and the literal `default`, which project settings routes accept as
	// an alias but the workload-key cache, the static key mint and the
	// platform-token exchange all need as a UUID — is resolved within the
	// selected org. Without an org there is nothing to resolve against: a
	// project-bound key with `--project` set to anything works regardless
	// of the value, so the override passes through verbatim rather than
	// building `/orgs//projects`.
	if a.Profile.OrgID == "" {
		return project, nil
	}
	resolved, err := a.ProjectResolver(ctx, a.Profile.OrgID, project)
	if err != nil {
		return "", err
	}
	if a.Profile.ProjectByOrg == nil {
		a.Profile.ProjectByOrg = map[string]string{}
	}
	a.Profile.ProjectByOrg[a.Profile.OrgID] = resolved
	return resolved, nil
}
