// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/auth"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/google/uuid"
)

func jsonMarshal(v any) ([]byte, error) { return json.Marshal(v) }

func projectTable(ps []gen.Project, active string) output.Table {
	t := output.Table{Headers: []string{"", "ID", "NAME", "CREATED"}}
	for _, p := range ps {
		mark := ""
		if p.Id.String() == active {
			mark = "*"
		}
		t.Rows = append(t.Rows, []string{mark, p.Id.String(), p.Name, p.CreatedAt.Format("2006-01-02")})
	}
	return t
}

func newProjectCmd() *cobra.Command {
	c := &cobra.Command{Use: "project", Short: "Projects in the selected organization: list, select, create, rename, delete, budget", GroupID: GroupManagement}
	var listFlags pageFlags
	list := &cobra.Command{
		Use: "list", Short: "List the projects you can reach in the selected organization", Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			org, err := a.RequireOrg(ctx, pickOrg(a))
			if err != nil {
				return err
			}
			mc, err := a.Client(ctx)
			if err != nil {
				return err
			}
			ps, err := mgmtListPage(ctx, listFlags, func(ctx context.Context, cursor string, limit int64) (mgmt.Page[gen.Project], error) {
				return mgmt.ProjectsPage(ctx, mc, org, cursor, limit)
			})
			if err != nil {
				return err
			}
			return listOutRaw(a.Printer(), ps, func(ps []gen.Project) output.Table { return projectTable(ps, a.Profile.ProjectID()) })
		},
	}
	addPageFlags(list, &listFlags)
	c.AddCommand(list)
	c.AddCommand(&cobra.Command{
		Use: "get [id|name]", Short: "Show one project (default: the selected one)", Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			mc, err := a.Client(ctx)
			if err != nil {
				return err
			}
			id := a.Profile.ProjectID()
			if len(args) == 1 {
				id, err = resolveProjectLookupID(cmd, mc, args[0])
				if err != nil {
					return err
				}
			}
			if id == "" {
				return cerr.MissingArg("a project", "an id/name argument or --project")
			}
			if len(args) == 0 && !looksLikeProjectUUID(id) {
				id, err = resolveProjectLookupID(cmd, mc, id)
				if err != nil {
					return err
				}
			}
			var p api.Raw[gen.Project]
			if err := mc.Do(ctx, http.MethodGet, "/projects/"+url.PathEscape(id), nil, nil, &p); err != nil {
				return err
			}
			return a.Printer().Object(p, [][2]string{{"ID", p.V.Id.String()}, {"Name", p.V.Name}, {"Organization", p.V.OrgId.String()}, {"Created", p.V.CreatedAt.Format("2006-01-02 15:04")}})
		},
	})
	c.AddCommand(&cobra.Command{
		Use: "use [id|name]", Short: "Select the project for following commands (remembered per organization)", Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			org, err := a.RequireOrg(ctx, pickOrg(a))
			if err != nil {
				return err
			}
			var id string
			if len(args) == 1 {
				mc, err := a.Client(ctx)
				if err != nil {
					return err
				}
				ps, err := mgmt.ListProjects(ctx, mc, org)
				if err != nil {
					return err
				}
				p, err := mgmt.ResolveProject(ps, args[0])
				if err != nil {
					return err
				}
				id = p.Id.String()
			} else {
				if a.Profile.ProjectByOrg != nil {
					delete(a.Profile.ProjectByOrg, org)
				}
				id, err = a.RequireProject(ctx, pickProject(a))
				if err != nil {
					return err
				}
			}
			err = a.Store.Update(func(c *config.Config, _ *config.Credentials) (bool, bool, error) {
				p, ok := c.Profile(a.Profile.Name)
				if !ok {
					return false, false, cerr.New(cerr.ExitAuth, "not logged in").WithHint("run `daytona login`")
				}
				if p.OrgID == "" {
					p.OrgID = org
				}
				if p.ProjectByOrg == nil {
					p.ProjectByOrg = map[string]string{}
				}
				p.ProjectByOrg[org] = id
				return true, false, nil
			})
			if err != nil {
				return err
			}
			a.Info("Project %s selected in organization %s.", id, org)
			return nil
		},
	})
	c.AddCommand(&cobra.Command{
		Use: "create <name>", Short: "Create a project in the selected organization",
		Long: "Create a project in the selected organization. The name is 1 to 64 characters after trimming, with no\ncontrol characters; `default` is reserved.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			if err := checkNewOrgProjectName("project", args[0]); err != nil {
				return err
			}
			org, err := a.RequireOrg(ctx, pickOrg(a))
			if err != nil {
				return err
			}
			mc, err := a.Client(ctx)
			if err != nil {
				return err
			}
			var p api.Raw[gen.Project]
			if err := mc.Do(ctx, http.MethodPost, "/orgs/"+url.PathEscape(org)+"/projects", nil, gen.CreateProjectRequest{Name: args[0]}, &p); err != nil {
				return err
			}
			return a.Printer().Object(p, [][2]string{{"ID", p.V.Id.String()}, {"Name", p.V.Name}})
		},
	})
	upd := &cobra.Command{
		Use: "update <id|name> --name <new>", Short: "Rename a project",
		Long: "Rename a project. The name is 1 to 64 characters after trimming, with no control characters, and\nnot `default`; the current name is accepted unchanged even when it is longer.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			ctx := cmd.Context()
			name, _ := cmd.Flags().GetString("name")
			if name == "" {
				return cerr.Usage("--name is required")
			}
			tooLong, err := checkOrgProjectName("project", name)
			if err != nil {
				return err
			}
			project, orgID, mc, err := resolveProjectID(cmd, args[0])
			if err != nil {
				return err
			}
			current := project.Name
			if looksLikeProjectUUID(args[0]) {
				current = ""
			}
			if err := checkRenamedProjectName(ctx, mc, orgID, project.Id.String(), current, name, tooLong); err != nil {
				return err
			}
			var p api.Raw[gen.Project]
			if err := mc.Do(ctx, http.MethodPatch, "/projects/"+url.PathEscape(project.Id.String()), nil, gen.UpdateProjectRequest{Name: name}, &p); err != nil {
				return err
			}
			return a.Printer().Object(p, [][2]string{{"ID", p.V.Id.String()}, {"Name", p.V.Name}})
		},
	}
	upd.Flags().String("name", "", "new name (1 to 64 characters)")
	c.AddCommand(upd)
	c.AddCommand(&cobra.Command{
		Use: "delete <id|name>", Short: "Delete a project (refused while it still holds resources)", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			if err := a.PreflightConfirm("delete", "project "+args[0]); err != nil {
				return err
			}
			project, orgID, mc, err := resolveProjectID(cmd, args[0])
			if err != nil {
				return err
			}
			target := project.Name
			if target == "" {
				target = project.Id.String()
			}
			if err := a.Confirm("delete", "project "+target); err != nil {
				return err
			}
			if err := mc.Do(cmd.Context(), http.MethodDelete, "/projects/"+url.PathEscape(project.Id.String()), nil, nil, nil); err != nil {
				return err
			}
			if err := forgetDeletedProject(a, orgID, project.Id.String()); err != nil {
				return err
			}
			return printAck(a, "id", project.Id.String(), "deleted", fmt.Sprintf("Project %s deleted.", project.Id.String()))
		},
	})
	c.AddCommand(newProjectBudgetCmd())
	return c
}

// resolveProjectID resolves an id-or-name within the selected org and returns
// the account API client too.
func resolveProjectID(cmd *cobra.Command, ref string) (*gen.Project, string, *mgmtClient, error) {
	a := FromContext(cmd.Context())
	ctx := cmd.Context()
	mc, err := a.Client(ctx)
	if err != nil {
		return nil, "", nil, err
	}
	org, err := a.RequireOrg(ctx, pickOrg(a))
	if err != nil {
		return nil, "", nil, err
	}
	if looksLikeProjectUUID(ref) {
		id, parseErr := uuid.Parse(ref)
		if parseErr != nil {
			return nil, "", nil, parseErr
		}
		return &gen.Project{Id: id, Name: ref}, org, mc, nil
	}
	ps, err := mgmt.ListProjects(ctx, mc, org)
	if err != nil {
		return nil, "", nil, err
	}
	p, err := mgmt.ResolveProject(ps, ref)
	if err != nil {
		return nil, "", nil, err
	}
	return p, org, mc, nil
}

type mgmtClient = api.Client

func resolveProjectLookupID(cmd *cobra.Command, mc *mgmtClient, ref string) (string, error) {
	if looksLikeProjectUUID(ref) {
		return ref, nil
	}
	a := FromContext(cmd.Context())
	org, err := a.RequireOrg(cmd.Context(), pickOrg(a))
	if err != nil {
		return "", err
	}
	ps, err := mgmt.ListProjects(cmd.Context(), mc, org)
	if err != nil {
		return "", err
	}
	p, err := mgmt.ResolveProject(ps, ref)
	if err != nil {
		return "", err
	}
	return p.Id.String(), nil
}

func looksLikeProjectUUID(ref string) bool {
	_, err := uuid.Parse(ref)
	return err == nil
}

func forgetDeletedProject(a *app.App, orgID, projectID string) error {
	if err := auth.ForgetWorkloadKeys(a, map[string]bool{projectID: true}); err != nil {
		return err
	}
	return a.Store.Update(func(c *config.Config, _ *config.Credentials) (bool, bool, error) {
		p, ok := c.Profile(a.Profile.Name)
		if !ok || p.ProjectByOrg == nil || p.ProjectByOrg[orgID] != projectID {
			return false, false, nil
		}
		delete(p.ProjectByOrg, orgID)
		return true, false, nil
	})
}
