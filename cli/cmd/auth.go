// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"fmt"
	"strings"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/auth"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/google/uuid"
	"github.com/spf13/cobra"
)

func init() {
	installAuth = func(a *app.App) {
		auth.Install(a)
		auth.InstallProjectResolver(a)
		auth.InstallWorkload(a)
		auth.InstallPlatformToken(a)
		// --org may be a NAME. Resolve it against the caller's orgs; an
		// unambiguous match yields the id, an ambiguous one is a usage error.
		// A value that matches nothing is passed through unchanged — the
		// server is the authority on ids it recognises, and a 404 from it is
		// the honest answer for an unknown reference.
		a.OrgResolver = func(ctx context.Context, ref string) (string, error) {
			mc, err := a.Client(ctx)
			if err != nil {
				return "", err
			}
			orgs, err := mgmt.ListOrgs(ctx, mc)
			if err != nil {
				// A 404 here means the route/tenancy is not served for this
				// credential — fall back to the raw value and let the target
				// route answer. A static credential (an org API key: the one
				// bearer admitted on `/orgs/{org_id}/audit-logs` but not on `GET
				// /orgs`) cannot list organizations, so a NAME cannot be
				// resolved for it — say so, instead of a misleading "run
				// `daytona login`". Anything else (network/5xx, or a 401/403
				// under a real login) is the real diagnosis and must surface.
				code := cerr.ExitCode(err)
				if code == cerr.ExitNotFound {
					return ref, nil
				}
				if (code == cerr.ExitAuth || code == cerr.ExitForbidden) && (a.HasExplicitCredential() || a.CredentialSource == "profile-token") {
					return "", cerr.Usage("--org %q is a name, and this credential cannot list organizations to resolve it", ref).
						WithHint("pass the organization id (UUID) with --org / DAYTONA_ORG, or use a user login")
				}
				return "", err
			}
			o, err := mgmt.ResolveOrg(orgs, ref)
			if err != nil {
				if cerr.ExitCode(err) == cerr.ExitNotFound {
					return ref, nil // no such name among the caller's orgs: the server judges
				}
				return "", err // ambiguous name: refuse rather than guess
			}
			return o.Id.String(), nil
		}
	}
	Register(newLoginCmd)
	Register(newLogoutCmd)
	Register(newWhoamiCmd)
}

func newWhoamiCmd() *cobra.Command {
	return &cobra.Command{
		Use:     "whoami",
		Short:   "Show who you are, which credential is in use, and the selected org/project",
		GroupID: GroupAuth,
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			orgID, orgRef, projectID, projectRef := whoamiScopeRefs(ctx, a)
			out := map[string]any{
				"profile":           a.Profile.Name,
				"api_url":           a.Profile.APIURL,
				"credential_source": a.CredentialSource,
				"org_id":            orgID,
				"project_id":        projectID,
			}
			if orgRef != "" {
				out["org_ref"] = orgRef
			}
			if projectRef != "" {
				out["project_ref"] = projectRef
			}
			kv := [][2]string{{"Profile", a.Profile.Name}, {"API", a.Profile.APIURL}, {"Credential", credentialLabel(a)}}
			if a.CredentialSource == "profile-oidc" {
				mc, err := a.Client(ctx)
				if err != nil {
					return err
				}
				me, err := mgmt.GetMe(ctx, mc)
				if err != nil {
					return err
				}
				out["user"] = me
				name := ""
				if me.Name != nil {
					name = *me.Name
				}
				kv = append(kv, [2]string{"User", strings.TrimSpace(me.Email + " " + name)})
				orgs, err := mgmt.ListOrgs(ctx, mc)
				if err != nil {
					return err // an outage or a 403 here is news, not something to hide
				}
				if orgID != "" {
					o, err := mgmt.ResolveOrg(orgs, orgID)
					if err != nil {
						return err // the selected org is gone or no longer visible
					}
					out["org_id"] = o.Id.String()
					out["org"] = o
					kv = append(kv, [2]string{"Organization", fmt.Sprintf("%s (%s) roles=%s", o.Name, o.Id.String(), strings.Join(o.Membership.Roles, ","))})
				}
			} else {
				kv = append(kv, [2]string{"Organization", orgID})
			}
			kv = append(kv, [2]string{"Project", projectID})
			return a.Printer().Object(out, kv)
		},
	}
}

func whoamiScopeRefs(ctx context.Context, a *app.App) (string, string, string, string) {
	orgID := strings.TrimSpace(a.Profile.OrgID)
	orgRef := ""
	if orgID != "" && !looksLikeUUIDRef(orgID) && a.OrgResolver != nil {
		resolvedOrgID, err := a.OrgResolver(ctx, orgID)
		orgRef = orgID
		if err == nil {
			// Re-key the remembered project under the id BEFORE reading it,
			// or `--org <name>` without `--project` reports no project while
			// `project use` had remembered one for exactly this org.
			if a.Profile.ProjectByOrg != nil {
				if project, ok := a.Profile.ProjectByOrg[orgID]; ok {
					delete(a.Profile.ProjectByOrg, orgID)
					a.Profile.ProjectByOrg[resolvedOrgID] = project
				}
			}
			a.Profile.OrgID = resolvedOrgID
			orgID = resolvedOrgID
		}
	}
	rawProject := strings.TrimSpace(a.Profile.ProjectID())
	projectID := rawProject
	projectRef := ""
	if rawProject != "" && !looksLikeUUIDRef(rawProject) && a.ProjectResolver != nil && orgID != "" {
		resolvedProjectID, err := a.ProjectResolver(ctx, orgID, rawProject)
		projectRef = rawProject
		if err == nil {
			projectID = resolvedProjectID
		}
	}
	return orgID, orgRef, projectID, projectRef
}

func looksLikeUUIDRef(value string) bool {
	_, err := uuid.Parse(strings.TrimSpace(value))
	return err == nil
}

func credentialLabel(a *app.App) string {
	switch a.CredentialSource {
	case "flag":
		return "static credential (--api-key)"
	case "env":
		return "static credential (DAYTONA_API_KEY)"
	case "profile-token":
		return "static credential (profile)"
	case "profile-oidc":
		return "user login (OIDC; sandbox routes use a short-lived platform token where the deployment issues one, else a minted project key)"
	}
	return "none — run `daytona login`"
}

// isSSHSession reports whether this process runs inside an SSH session (the
// SSH_CONNECTION / SSH_TTY variables sshd sets). Advisory only: it selects
// the login flow that can succeed, never anything security-relevant.
func isSSHSession(getenv func(string) string) bool {
	return getenv("SSH_CONNECTION") != "" || getenv("SSH_TTY") != "" || getenv("SSH_CLIENT") != ""
}
