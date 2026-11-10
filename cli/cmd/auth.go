// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"fmt"
	"strings"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/auth"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/spf13/cobra"
)

func init() {
	installAuth = func(a *app.App) {
		auth.Install(a)
		auth.InstallProjectResolver(a)
		auth.InstallWorkload(a)
		auth.InstallPlatformToken(a)
		a.StaticKeyProjectFn = func(ctx context.Context) (app.StaticKeyProject, bool, error) {
			if !a.HasExplicitCredential() && a.CredentialSource != "profile-token" {
				return app.StaticKeyProject{}, false, nil
			}
			mc, err := a.Client(ctx)
			if err != nil {
				return app.StaticKeyProject{}, false, err
			}
			key, err := mgmt.CurrentProjectKey(ctx, mc)
			if err != nil {
				switch cerr.ExitCode(err) {
				case cerr.ExitAuth, cerr.ExitForbidden, cerr.ExitNotFound:
					// Not a project API key (or this API does not describe keys).
					return app.StaticKeyProject{}, false, nil
				}
				return app.StaticKeyProject{}, false, err
			}
			return app.StaticKeyProject{
				ID: key.ProjectId.String(), Name: key.Context.ProjectName,
				OrgID: key.Context.OrgId.String(), OrgName: key.Context.OrgName,
			}, true, nil
		}
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
		Use:   "whoami",
		Short: "Show who you are, which credential is in use, and the selected org/project",
		Long: `Show who you are, which credential is in use, and the selected organization
and project. org_id and project_id hold resolved IDs only; a selection given by
name is reported in org_ref / project_ref, and context_resolved is false when a
name could not be resolved to an ID.`,
		GroupID: GroupAuth,
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			scope := whoamiScopeRefs(ctx, a)
			out := map[string]any{
				"profile":          a.Profile.Name,
				"api_url":          a.Profile.APIURL,
				"context_resolved": scope.resolved,
			}
			if a.CredentialSource != "" { // omitted with no credential
				out["credential_source"] = a.CredentialSource
			}
			if scope.orgID != "" {
				out["org_id"] = scope.orgID
			}
			if scope.projectID != "" {
				out["project_id"] = scope.projectID
			}
			if scope.orgRef != "" {
				out["org_ref"] = scope.orgRef
			}
			if scope.projectRef != "" {
				out["project_ref"] = scope.projectRef
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
				if me.V.Name != nil {
					name = *me.V.Name
				}
				kv = append(kv, [2]string{"User", strings.TrimSpace(me.V.Email + " " + name)})
				orgs, err := mgmt.ListOrgsRaw(ctx, mc)
				if err != nil {
					return err // an outage or a 403 here is news, not something to hide
				}
				if scope.orgID != "" {
					o, err := mgmt.ResolveOrg(api.Values(orgs), scope.orgID)
					if err != nil {
						return err // the selected org is gone or no longer visible
					}
					out["org_id"] = o.Id.String()
					for _, raw := range orgs {
						if raw.V.Id == o.Id {
							out["org"] = raw // the API's representation
							break
						}
					}
					kv = append(kv, [2]string{"Organization", fmt.Sprintf("%s (%s) roles=%s", o.Name, o.Id.String(), strings.Join(o.Membership.Roles, ","))})
				} else {
					kv = append(kv, [2]string{"Organization", scopeLabel(scope.orgID, scope.orgRef)})
				}
			} else if a.HasExplicitCredential() || a.CredentialSource == "profile-token" {
				key, ok, err := whoamiStaticKey(ctx, a, &scope)
				if err != nil {
					return err
				}
				if ok {
					out["api_key"] = key
					out["org_id"], out["project_id"] = scope.orgID, scope.projectID
					delete(out, "org_ref")
					delete(out, "project_ref")
					out["context_resolved"] = true
					kv = append(kv, [2]string{"Organization", fmt.Sprintf("%s (%s)", key.V.Context.OrgName, scope.orgID)})
					kv = append(kv, [2]string{"Project", fmt.Sprintf("%s (%s)", key.V.Context.ProjectName, scope.projectID)})
					return a.Printer().Object(out, kv)
				}
				kv = append(kv, [2]string{"Organization", scopeLabel(scope.orgID, scope.orgRef)})
			} else {
				kv = append(kv, [2]string{"Organization", scopeLabel(scope.orgID, scope.orgRef)})
			}
			kv = append(kv, [2]string{"Project", scopeLabel(scope.projectID, scope.projectRef)})
			return a.Printer().Object(out, kv)
		},
	}
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
