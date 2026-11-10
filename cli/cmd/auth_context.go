// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"net/http"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/google/uuid"
)

// whoamiStaticKey describes a static credential through GET /api-keys/current:
// a refused credential is an authentication error (exit 3); a credential that
// is not a project API key (403/404) is reported without a context. A project
// key fills the scope with its own organization and project, and a --project /
// --org selection that names another one is refused, as on every command.
func whoamiStaticKey(ctx context.Context, a *app.App, scope *whoamiScope) (api.Raw[gen.ProjectApiKeyWithContext], bool, error) {
	var key api.Raw[gen.ProjectApiKeyWithContext]
	mc, err := a.Client(ctx)
	if err != nil {
		return key, false, err
	}
	if err := mc.Do(ctx, http.MethodGet, "/api-keys/current", nil, nil, &key); err != nil {
		switch cerr.ExitCode(err) {
		case cerr.ExitForbidden, cerr.ExitNotFound:
			return key, false, nil
		}
		return key, false, err
	}
	keyOrg, keyProject := key.V.Context.OrgId.String(), key.V.ProjectId.String()
	if ref := strings.TrimSpace(firstNonBlank(scope.orgID, scope.orgRef)); a.OrgFromOverride() && ref != "" &&
		!strings.EqualFold(ref, keyOrg) && !strings.EqualFold(ref, key.V.Context.OrgName) {
		return key, false, cerr.Usage("--org %q is not the organization this API key acts in (%s, %s)", ref, key.V.Context.OrgName, keyOrg)
	}
	if ref := strings.TrimSpace(firstNonBlank(scope.projectID, scope.projectRef)); a.ProjectFromOverride() && ref != "" &&
		!strings.EqualFold(ref, keyProject) && !strings.EqualFold(ref, key.V.Context.ProjectName) {
		return key, false, cerr.Usage("--project %q is not the project this API key acts in (%s, %s)", ref, key.V.Context.ProjectName, keyProject).
			WithHint("a project API key always acts in its own project: drop --project / DAYTONA_PROJECT, or use a key of project %s", ref)
	}
	scope.orgID, scope.projectID = keyOrg, keyProject
	return key, true, nil
}

func firstNonBlank(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

// whoamiScope is the selected organization and project: IDs only when
// resolved, the name as given in the *Ref fields.
type whoamiScope struct {
	orgID, orgRef, projectID, projectRef string
	resolved                             bool
}

func scopeLabel(id, ref string) string {
	switch {
	case id != "":
		return id
	case ref != "":
		return ref + " (unresolved)"
	}
	return ""
}

func whoamiScopeRefs(ctx context.Context, a *app.App) whoamiScope {
	scope := whoamiScope{resolved: true}
	rawOrg := strings.TrimSpace(a.Profile.OrgID)
	switch {
	case rawOrg == "":
	case looksLikeUUIDRef(rawOrg):
		scope.orgID = rawOrg
	default:
		scope.orgRef = rawOrg
		if a.OrgResolver == nil {
			scope.resolved = false
			break
		}
		// A resolver passes a name it cannot resolve through unchanged; only
		// an ID it returns counts as resolved.
		resolvedOrgID, err := a.OrgResolver(ctx, rawOrg)
		if err != nil || !looksLikeUUIDRef(resolvedOrgID) {
			scope.resolved = false
			break
		}
		// Re-key the remembered project under the id BEFORE reading it,
		// or `--org <name>` without `--project` reports no project while
		// `project use` had remembered one for exactly this org.
		if a.Profile.ProjectByOrg != nil {
			if project, ok := a.Profile.ProjectByOrg[rawOrg]; ok {
				delete(a.Profile.ProjectByOrg, rawOrg)
				a.Profile.ProjectByOrg[resolvedOrgID] = project
			}
		}
		a.Profile.OrgID = resolvedOrgID
		scope.orgID = resolvedOrgID
	}
	rawProject := strings.TrimSpace(a.Profile.ProjectID())
	switch {
	case rawProject == "":
	case looksLikeUUIDRef(rawProject):
		scope.projectID = rawProject
	default:
		scope.projectRef = rawProject
		if a.ProjectResolver == nil || scope.orgID == "" {
			scope.resolved = false
			break
		}
		resolvedProjectID, err := a.ProjectResolver(ctx, scope.orgID, rawProject)
		if err != nil || !looksLikeUUIDRef(resolvedProjectID) {
			scope.resolved = false
			break
		}
		scope.projectID = resolvedProjectID
	}
	return scope
}

func looksLikeUUIDRef(value string) bool {
	_, err := uuid.Parse(strings.TrimSpace(value))
	return err == nil
}
