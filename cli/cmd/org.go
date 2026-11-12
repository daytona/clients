// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/auth"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/ui"
	"github.com/google/uuid"
)

var selectPrompt = ui.SelectContext

func init() {
	Register(newOrgCmd)
	Register(newProjectCmd)
}

// pickOrg is the interactive org picker.
func pickOrg(a *app.App) func(ctx context.Context) (string, error) {
	return func(ctx context.Context) (string, error) {
		mc, err := a.Client(ctx)
		if err != nil {
			return "", err
		}
		orgs, err := mgmt.ListOrgs(ctx, mc)
		if err != nil {
			return "", err
		}
		opts := buildOrgPickerOptions(orgs)
		if len(opts) == 0 {
			return "", cerr.New(cerr.ExitNotFound, "you are not a member of any organization")
		}
		return selectPrompt(ctx, "Select an organization", opts)
	}
}

// pickProject is the interactive project picker within the selected org.
func pickProject(a *app.App) func(ctx context.Context) (string, error) {
	return func(ctx context.Context) (string, error) {
		org, err := a.RequireOrg(ctx, pickOrg(a))
		if err != nil {
			return "", err
		}
		mc, err := a.Client(ctx)
		if err != nil {
			return "", err
		}
		ps, err := mgmt.ListProjects(ctx, mc, org)
		if err != nil {
			return "", err
		}
		opts := buildProjectPickerOptions(ps)
		if len(opts) == 0 {
			return "", cerr.New(cerr.ExitNotFound, "no projects in this organization")
		}
		projectID, err := selectPrompt(ctx, "Select a project", opts)
		if err != nil {
			return "", err
		}
		if a.Profile != nil {
			a.Profile.OrgID = org
			if a.Profile.ProjectByOrg == nil {
				a.Profile.ProjectByOrg = map[string]string{}
			}
			a.Profile.ProjectByOrg[org] = projectID
		}
		return projectID, nil
	}
}

func buildOrgPickerOptions(orgs []gen.OrganizationListItem) []ui.Option {
	opts := make([]ui.Option, 0, len(orgs))
	for _, o := range orgs {
		label := output.SanitizeLine(o.Name)
		if o.Personal {
			label += " (personal)"
		}
		opts = append(opts, ui.Option{Label: label, Value: o.Id.String()})
	}
	return opts
}

func buildProjectPickerOptions(projects []gen.Project) []ui.Option {
	opts := make([]ui.Option, 0, len(projects))
	for _, p := range projects {
		opts = append(opts, ui.Option{Label: output.SanitizeLine(p.Name), Value: p.Id.String()})
	}
	return opts
}

func orgTable(os []gen.OrganizationListItem, active string) output.Table {
	t := output.Table{Headers: []string{"", "ID", "NAME", "ROLES", "PERSONAL"}}
	for _, o := range os {
		mark := ""
		if o.Id.String() == active {
			mark = "*"
		}
		personal := ""
		if o.Personal {
			personal = "yes"
		}
		t.Rows = append(t.Rows, []string{mark, o.Id.String(), o.Name, joinComma(o.Membership.Roles), personal})
	}
	return t
}

func joinComma(s []string) string {
	out := ""
	for i, v := range s {
		if i > 0 {
			out += ","
		}
		out += v
	}
	return out
}

func newOrgCmd() *cobra.Command {
	c := &cobra.Command{Use: "org", Short: "Organizations: list, select, create, rename, delete, leave, limits", GroupID: GroupManagement}
	var listFlags pageFlags
	list := &cobra.Command{
		Use: "list", Short: "List the organizations you belong to", Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			mc, err := a.Client(cmd.Context())
			if err != nil {
				return err
			}
			orgs, err := mgmtListPage(cmd.Context(), listFlags, func(ctx context.Context, cursor string, limit int64) (mgmt.Page[gen.OrganizationListItem], error) {
				return mgmt.OrgsPage(ctx, mc, cursor, limit)
			})
			if err != nil {
				return err
			}
			return listOutRaw(a.Printer(), orgs, func(os []gen.OrganizationListItem) output.Table { return orgTable(os, a.Profile.OrgID) })
		},
	}
	addPageFlags(list, &listFlags)
	c.AddCommand(list)
	c.AddCommand(&cobra.Command{
		Use: "get [id|name]", Short: "Show one organization (default: the selected one)", Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			mc, err := a.Client(cmd.Context())
			if err != nil {
				return err
			}
			ref := a.Profile.OrgID
			if len(args) == 1 {
				ref = args[0]
			}
			if ref == "" {
				return cerr.MissingArg("an organization", "an id/name argument or --org")
			}
			orgID, err := resolveOrgID(cmd.Context(), a, mc, ref)
			if err != nil {
				return err
			}
			o, err := mgmt.GetOrg(cmd.Context(), mc, orgID)
			if err != nil {
				return err
			}
			return a.Printer().Object(o, [][2]string{{"ID", o.V.Id.String()}, {"Name", o.V.Name}, {"Personal", strconv.FormatBool(o.V.Personal)}, {"Suspended", strconv.FormatBool(o.V.Suspended)}, {"Created", o.V.CreatedAt.Format("2006-01-02 15:04")}, {"Updated", o.V.UpdatedAt.Format("2006-01-02 15:04")}})
		},
	})
	c.AddCommand(&cobra.Command{
		Use: "limits [id|name]", Short: "Show the organization's limits and current usage (default: the selected one)", Args: cobra.MaximumNArgs(1),
		Long: `Show the organization's limits — sandboxes, image
builds, Mac reservations, members, images, snapshots, secrets and API keys —
each with the current usage it is checked against; "unlimited" means no limit
applies. A request refused by one of them — a Mac reservation purchase
included (reservation create or renew, or a sandbox create-macos or start
that buys a Mac) — exits 6 with "LIMIT_EXCEEDED" naming the limit, the current count and the
maximum. Requires read:organization, or read:limits with an API key. An API
key reads its own organization when none is named, and its read leaves out
the member counts.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			mc, err := a.Client(ctx)
			if err != nil {
				return err
			}
			orgID, err := limitsOrgID(ctx, a, mc, args)
			if err != nil {
				return err
			}
			lim, err := mgmt.GetOrgLimits(ctx, mc, orgID)
			if err != nil {
				return err
			}
			return a.Printer().Object(lim.Wire, limitsKV(lim))
		},
	})
	c.AddCommand(&cobra.Command{
		Use: "use [id|name]", Short: "Select the organization for following commands", Args: cobra.MaximumNArgs(1),
		Long: `Selects an organization and restores the project you last used in it (or
its default project). Accepts an id or an unambiguous name; with no argument
shows a picker at a terminal.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			mc, err := a.Client(ctx)
			if err != nil {
				return err
			}
			orgs, err := mgmt.ListOrgs(ctx, mc)
			if err != nil {
				return err
			}
			var id string
			var selected *gen.OrganizationListItem
			if len(args) == 1 {
				o, err := mgmt.ResolveOrg(orgs, args[0])
				if err != nil {
					return err
				}
				id = o.Id.String()
				selected = o
			} else {
				a.Profile.OrgID = ""
				id, err = a.RequireOrg(ctx, pickOrg(a))
				if err != nil {
					return err
				}
				selected, err = mgmt.ResolveOrg(orgs, id)
				if err != nil {
					return err
				}
			}
			ps, err := mgmt.ListProjects(ctx, mc, id)
			if err != nil {
				return err
			}
			var chosen string
			err = a.Store.Update(func(c *config.Config, _ *config.Credentials) (bool, bool, error) {
				p, ok := c.Profile(a.Profile.Name)
				if !ok {
					return false, false, cerr.New(cerr.ExitAuth, "not logged in").WithHint("run `daytona login`")
				}
				p.OrgID = id
				if p.ProjectByOrg == nil {
					p.ProjectByOrg = map[string]string{}
				}
				if remembered := p.ProjectByOrg[id]; remembered != "" {
					if _, err := mgmt.ResolveProject(ps, remembered); err != nil {
						return false, false, cerr.New(cerr.ExitNotFound, "the remembered project %s is no longer accessible in this organization", remembered).
							WithHint("run `daytona project use <id|name>` to pick another")
					}
					chosen = remembered
				} else if selected != nil && selected.Membership.DefaultProjectId != uuid.Nil {
					chosen = selected.Membership.DefaultProjectId.String()
					p.ProjectByOrg[id] = chosen
				}
				return true, false, nil
			})
			if err != nil {
				return err
			}
			a.Info("Organization %s selected%s.", id, projectLabel(chosen))
			return nil
		},
	})
	c.AddCommand(&cobra.Command{
		Use: "create <name>", Short: "Create an organization",
		Long: "Create an organization. The name is 1 to 64 characters after trimming, with no control characters.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			if err := checkOrgProjectName("organization", args[0]); err != nil {
				return err
			}
			mc, err := a.Client(cmd.Context())
			if err != nil {
				return err
			}
			var out api.Raw[gen.Organization]
			if err := mc.Do(cmd.Context(), http.MethodPost, "/orgs", nil, gen.CreateOrganizationRequest{Name: args[0]}, &out); err != nil {
				return err
			}
			return a.Printer().Object(out, [][2]string{{"id", out.V.Id.String()}, {"name", out.V.Name}})
		},
	})
	upd := &cobra.Command{
		Use: "update <id|name> --name <new>", Short: "Rename an organization",
		Long: "Rename an organization. The name is 1 to 64 characters after trimming, with no control characters;\na rename to the current name is sent and left to the API.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			name, _ := cmd.Flags().GetString("name")
			if name == "" {
				return cerr.Usage("--name is required")
			}
			refusal := checkOrgProjectName("organization", name)
			if strings.TrimSpace(name) == "" {
				// A blank name is never the current one.
				return refusal
			}
			mc, err := a.Client(cmd.Context())
			if err != nil {
				return err
			}
			id, err := resolveOrgID(cmd.Context(), a, mc, args[0])
			if err != nil {
				return err
			}
			if err := checkRenamedOrgName(cmd.Context(), mc, id, name, refusal); err != nil {
				return err
			}
			var out api.Raw[gen.Organization]
			if err := mc.Do(cmd.Context(), http.MethodPatch, "/orgs/"+url.PathEscape(id), nil, gen.UpdateOrganizationRequest{Name: mgmt.Ref(name)}, &out); err != nil {
				return err
			}
			return a.Printer().Object(out, [][2]string{{"id", out.V.Id.String()}, {"name", out.V.Name}})
		},
	}
	upd.Flags().String("name", "", "new name (1 to 64 characters)")
	c.AddCommand(upd)
	c.AddCommand(&cobra.Command{
		Use: "delete <id|name>", Short: "Delete an organization (personal orgs cannot be deleted)", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			if err := a.PreflightConfirm("delete", "organization "+args[0]); err != nil {
				return err
			}
			mc, err := a.Client(ctx)
			if err != nil {
				return err
			}
			id, err := resolveOrgID(ctx, a, mc, args[0])
			if err != nil {
				return err
			}
			drop, lookupFailed, err := orgLeaveProjectIDs(ctx, a, mc, id)
			if err != nil {
				return err
			}
			if err := a.Confirm("delete", "organization "+args[0]); err != nil {
				return err
			}
			if err := mc.Do(ctx, http.MethodDelete, "/orgs/"+url.PathEscape(id), nil, nil, nil); err != nil {
				return err
			}
			if err := finalizeOrgLocalState(a, id, drop, "deleted"); err != nil {
				return err
			}
			if lookupFailed {
				a.Info("warning: deleted organization %s on the server, but the CLI could not list its projects during cleanup; cached credentials for other projects in that org may remain. Run `daytona logout` to clear local cached credentials.", id)
			}
			return printAck(a, "id", id, "deleted", fmt.Sprintf("Organization %s deleted.", id))
		},
	})
	c.AddCommand(&cobra.Command{
		Use: "leave [id|name]", Short: "Leave an organization (revokes the API keys you minted in it)", Long: "Leave an organization (revokes the API keys you minted in it). The CLI also forgets cached project keys and platform tokens for that organization. If it cannot list the org's projects during cleanup, it forgets the remembered project for that org and warns that other cached credentials may remain; run `daytona logout` to clear them all.", Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			preTarget := a.Profile.OrgID
			if len(args) == 1 {
				preTarget = args[0]
			}
			if err := a.PreflightConfirm("leave", "organization "+preTarget); err != nil {
				return err
			}
			mc, err := a.Client(ctx)
			if err != nil {
				return err
			}
			ref := a.Profile.OrgID
			if len(args) == 1 {
				ref = args[0]
			}
			if ref == "" {
				return cerr.MissingArg("an organization", "an id/name argument or --org")
			}
			id, err := resolveOrgID(ctx, a, mc, ref)
			if err != nil {
				return err
			}
			me, err := mgmt.GetMe(ctx, mc)
			if err != nil {
				return err
			}
			if err := a.Confirm("leave", "organization "+ref+" (your minted API keys in it will be revoked)"); err != nil {
				return err
			}
			drop, lookupFailed, err := orgLeaveProjectIDs(ctx, a, mc, id)
			if err != nil {
				return err
			}
			if err := mc.Do(ctx, http.MethodDelete, "/orgs/"+url.PathEscape(id)+"/members/"+url.PathEscape(me.V.Id.String()), nil, nil, nil); err != nil {
				return err
			}
			if err := finalizeOrgLocalState(a, id, drop, "left"); err != nil {
				return err
			}
			if lookupFailed {
				a.Info("warning: left organization %s on the server, but the CLI could not list its projects during cleanup; cached credentials for other projects in that org may remain. Run `daytona logout` to clear local cached credentials.", id)
			}
			return printAck(a, "id", id, "left", "")
		},
	})
	return c
}

// limitsOrgID resolves the organization `org limits` reads. A project API key
// reads only its own organization, which it learns from GET /api-keys/current:
// no reference named on this call (argument, --org, DAYTONA_ORG) means that
// organization — a saved profile selection is ignored — and a name must be its
// name (listing organizations needs a login). Every other credential resolves
// as usual.
func limitsOrgID(ctx context.Context, a *app.App, mc *mgmtClient, args []string) (string, error) {
	ref := ""
	if len(args) == 1 {
		ref = args[0]
	} else if a.OrgFromOverride() {
		ref = a.Profile.OrgID
	}
	ref = strings.TrimSpace(ref)
	if _, err := uuid.Parse(ref); err == nil {
		return resolveOrgID(ctx, a, mc, ref)
	}
	key, err := staticProjectKey(ctx, a, mc)
	if err != nil {
		return "", err
	}
	if key == nil {
		if ref == "" {
			ref = a.Profile.OrgID
		}
		if ref == "" {
			return "", cerr.MissingArg("an organization", "an id/name argument or --org")
		}
		return resolveOrgID(ctx, a, mc, ref)
	}
	if ref != "" && !strings.EqualFold(ref, key.Context.OrgName) {
		return "", cerr.Usage("an API key reads only its own organization (%s)", key.Context.OrgName)
	}
	return key.Context.OrgId.String(), nil
}

// staticProjectKey describes the static credential (--api-key,
// DAYTONA_API_KEY, or a token saved with `daytona login --token`) when the
// server knows it as a project API key, and returns nil otherwise. The CLI
// never inspects a static credential: GET /api-keys/current
// answers 401/403 for anything that is not a project key. A saved login is
// never sent here.
func staticProjectKey(ctx context.Context, a *app.App, mc *mgmtClient) (*gen.ProjectApiKeyWithContext, error) {
	if !a.HasExplicitCredential() && a.CredentialSource != "profile-token" {
		return nil, nil
	}
	key, err := mgmt.CurrentProjectKey(ctx, mc)
	if code := cerr.ExitCode(err); code == cerr.ExitAuth || code == cerr.ExitForbidden {
		return nil, nil
	}
	return key, err
}

func resolveOrgID(ctx context.Context, _ *app.App, mc *mgmtClient, ref string) (string, error) {
	if parsed, err := uuid.Parse(strings.TrimSpace(ref)); err == nil {
		return parsed.String(), nil
	}
	orgs, err := mgmt.ListOrgs(ctx, mc)
	if err != nil {
		return "", err
	}
	o, err := mgmt.ResolveOrg(orgs, ref)
	if err != nil {
		return "", err
	}
	return o.Id.String(), nil
}

func orgLeaveProjectIDs(ctx context.Context, a *app.App, mc *mgmtClient, orgID string) (map[string]bool, bool, error) {
	ps, err := mgmt.ListProjects(ctx, mc, orgID)
	if err == nil {
		drop := make(map[string]bool, len(ps))
		for _, p := range ps {
			drop[p.Id.String()] = true
		}
		return drop, false, nil
	}
	drop := map[string]bool{}
	if remembered := a.Profile.ProjectByOrg[orgID]; remembered != "" {
		drop[remembered] = true
	}
	return drop, true, nil
}

func finalizeOrgLocalState(a *app.App, orgID string, projectIDs map[string]bool, action string) error {
	if err := auth.ForgetWorkloadKeys(a, projectIDs); err != nil {
		return orgLocalCleanupError(orgID, action, err)
	}
	if err := a.Store.Update(func(c *config.Config, _ *config.Credentials) (bool, bool, error) {
		p, ok := c.Profile(a.Profile.Name)
		if !ok {
			return false, false, nil
		}
		changed := false
		if p.OrgID == orgID {
			p.OrgID = ""
			changed = true
		}
		if p.ProjectByOrg != nil {
			if _, ok := p.ProjectByOrg[orgID]; ok {
				delete(p.ProjectByOrg, orgID)
				changed = true
			}
		}
		return changed, false, nil
	}); err != nil {
		return orgLocalCleanupError(orgID, action, err)
	}
	return nil
}

func orgLocalCleanupError(orgID, action string, err error) error {
	return cerr.Wrap(cerr.ExitFailure, err, "%s organization %s on the server, but failed to update local state", action, orgID).
		WithHint("run `daytona logout` to clear local cached credentials and selection")
}

func toString(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case nil:
		return ""
	case []any:
		s := ""
		for i, e := range t {
			if i > 0 {
				s += ","
			}
			s += toString(e)
		}
		return s
	default:
		b, _ := jsonMarshal(v)
		return string(b)
	}
}

func limitsKV(l *mgmt.OrgLimits) [][2]string {
	// Fields the response omits are not printed as zero.
	rows := [][2]string{
		{"Concurrent sandboxes", usageLimitString(l.Sandboxes.Concurrent)},
		{"Total sandboxes", usageLimitString(l.Sandboxes.Total)},
	}
	rows = append(rows,
		[2]string{"Concurrent image builds", usageLimitString(l.ImageBuilds.Concurrent)},
	)
	// Per-sandbox maximums of a Linux sandbox: the plan's cap, bounded by
	// the platform maximum. Omit the group when it is absent from the response.
	if r := l.SandboxResources; r != nil {
		rows = append(rows,
			[2]string{"Cores per sandbox", "max " + formatCores(r.MaxCores)},
			[2]string{"Memory per sandbox (MiB)", fmt.Sprintf("max %d", r.MaxMemoryMib)},
		)
	}
	// Seat cap: undecided invitations count at
	// invitation time, so they are shown beside the seated members. An API
	// key's read carries no member group.
	if l.Members != nil {
		rows = append(rows, [2]string{"Members", membersLimit(*l.Members)})
	}
	if l.HasStorage {
		rows = append(rows,
			[2]string{"Images", usageLimitString(l.Storage.Images)},
			[2]string{"Snapshots", usageLimitString(l.Storage.Snapshots)},
		)
	}
	if m := l.MacReservations; m != nil {
		rows = append(rows, [2]string{"Mac reservations", usageLimitString(*m)})
	}
	rows = append(rows,
		[2]string{"API keys per organization", usageLimitString(l.ApiKeysPerOrganization)},
		[2]string{"API keys per project", usageLimitString(l.ApiKeysPerProject)},
		[2]string{"Secrets per project", usageLimitString(l.SecretsPerProject)},
	)
	return rows
}

func usageLimitString(q gen.UsageLimit) string {
	if q.Limit == nil {
		return fmt.Sprintf("%d / unlimited", q.Usage)
	}
	return fmt.Sprintf("%d / %d", q.Usage, *q.Limit)
}

func membersLimit(m gen.MemberLimits) string {
	current := fmt.Sprintf("%d", m.Usage)
	if m.PendingInvitations > 0 {
		current = fmt.Sprintf("%d + %d pending", m.Usage, m.PendingInvitations)
	}
	if m.Limit == nil {
		return current + " / unlimited"
	}
	return fmt.Sprintf("%s / %d", current, *m.Limit)
}
