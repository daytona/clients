// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"net/http"
	"net/url"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/spf13/cobra"
)

func init() { Register(newRegistryCmd) }

func newRegistryCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "registry", Short: "Project registries: set, list, get, delete", GroupID: GroupManagement}
	cmd.AddCommand(newRegistrySetCmd(), newRegistryListCmd(), newRegistryGetCmd(), newRegistryDeleteCmd())
	return cmd
}

func newRegistrySetCmd() *cobra.Command {
	var username, password, kind string
	var passwordStdin bool
	var description string
	cmd := &cobra.Command{Use: "set <host>", Short: "Create or update a registry credential", Long: "Create or update a registry credential and print it (never its password). It reads the stored credential first to preserve omitted username, kind and description, so it needs read as well as write access. A new registry requires --username. The API requires a password on every set, including metadata-only updates, except for ecr_role; passwords cannot be read back or preserved by this endpoint.", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		if !gen.SetRegistryRequestKind(kind).Valid() {
			return cerr.Usage("--kind must be basic, ghcr, gcr, ecr or ecr_role")
		}
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		client, _, err := a.WorkloadClient(ctx, pickProject(a))
		if err != nil {
			return err
		}
		path := "/registries/" + url.PathEscape(args[0])
		var stored gen.Registry
		err = client.Do(ctx, http.MethodGet, path, nil, nil, &stored)
		if err != nil && cerr.ExitCode(err) != cerr.ExitNotFound {
			return err
		}
		var existing *gen.Registry
		if err == nil {
			existing = &stored
		}
		// kind ecr_role names an IAM role (--username) and carries no secret.
		// Without --kind the existing kind carries over, so a
		// rotation of an existing ecr_role registry needs no password either.
		effectiveKind := kind
		if !cmd.Flags().Changed("kind") && existing != nil && existing.Kind != "" {
			effectiveKind = string(existing.Kind)
		}
		roleOnly := effectiveKind == "ecr_role" && !cmd.Flags().Changed("password") && !passwordStdin
		var secret *string
		if !roleOnly {
			if err := mustOneOf(btoi(cmd.Flags().Changed("password"))+btoi(passwordStdin), "--password", "--password-stdin"); err != nil {
				return err
			}
			if cmd.Flags().Changed("password") {
				FromContext(cmd.Context()).Info("note: --password puts the credential in the process list and shell history; prefer --password-stdin")
			}
			value, err := secretInputValue(password, "", false, passwordStdin)
			if err != nil {
				return err
			}
			secret = &value
		}
		putReq, err := registryPutReq(cmd, existing, registrySetInput{Kind: kind, Username: username, Password: secret, Description: description})
		if err != nil {
			return err
		}
		var saved api.Raw[gen.Registry]
		if err := client.Do(ctx, http.MethodPut, path, nil, putReq, &saved); err != nil {
			return err
		}
		return printRegistry(a, saved)
	}}
	cmd.Flags().StringVar(&username, "username", "", "registry username (required on create; omitted on update keeps the stored username)")
	cmd.Flags().StringVar(&password, "password", "", "registry password or token on the command line — visible via the process list and shell history; prefer --password-stdin")
	cmd.Flags().BoolVar(&passwordStdin, "password-stdin", false, "read the password from stdin")
	cmd.Flags().StringVar(&kind, "kind", "basic", "registry auth kind (basic, ghcr, gcr, ecr, ecr_role; ecr_role takes the IAM role ARN as --username and no password)")
	cmd.Flags().StringVar(&description, "description", "", "registry description (set to an empty string to clear it)")
	return cmd
}

func newRegistryListCmd() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{Use: "list", Short: "List project registries (metadata only)", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		client, _, err := a.WorkloadClient(ctx, pickProject(a))
		if err != nil {
			return err
		}
		fetch := func(ctx context.Context, cursor string) (output.List[api.Raw[gen.Registry]], error) {
			return getRawList[gen.Registry](ctx, client, "/registries", listQuery(flags, cursor), "registries")
		}
		var items output.List[api.Raw[gen.Registry]]
		if flags.all {
			items, err = collectPaged(ctx, flags.cursor, fetch)
		} else {
			items, err = fetch(ctx, flags.cursor)
		}
		if err != nil {
			return err
		}
		return listOutRaw(a.Printer(), listWithoutWriteOnly(items, writeOnlyFields.registry), func(rows []gen.Registry) output.Table {
			tableRows := make([][]string, 0, len(rows))
			for _, row := range rows {
				tableRows = append(tableRows, []string{row.Host, string(row.Kind), row.Username, row.UpdatedAt.Format(time.RFC3339)})
			}
			return output.Table{Headers: []string{"HOST", "KIND", "USERNAME", "UPDATED_AT"}, Rows: tableRows}
		})
	}}
	addPageFlags(cmd, &flags)
	return cmd
}

func newRegistryGetCmd() *cobra.Command {
	return &cobra.Command{Use: "get <host>", Short: "Show one registry credential's metadata", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		client, _, err := a.WorkloadClient(ctx, pickProject(a))
		if err != nil {
			return err
		}
		var raw api.Raw[gen.Registry]
		if err := client.Do(ctx, http.MethodGet, "/registries/"+url.PathEscape(args[0]), nil, nil, &raw); err != nil {
			return err
		}
		a.Info("Registry passwords are write-only and are never returned by the API.")
		return printRegistry(a, raw)
	}}
}

func newRegistryDeleteCmd() *cobra.Command {
	return &cobra.Command{Use: "delete <host>", Short: "Delete a registry credential", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		a := FromContext(cmd.Context())
		if err := a.Confirm("delete", "registry "+args[0]); err != nil {
			return err
		}
		ctx := cmd.Context()
		client, _, err := a.WorkloadClient(ctx, pickProject(a))
		if err != nil {
			return err
		}
		if err := client.Do(ctx, http.MethodDelete, "/registries/"+url.PathEscape(args[0]), nil, nil, nil); err != nil {
			return err
		}
		return printAck(a, "host", args[0], "deleted", "")
	}}
}

type registrySetInput struct {
	Kind        string
	Username    string
	Password    *string
	Description string
}

func registryPutReq(cmd *cobra.Command, existing *gen.Registry, input registrySetInput) (gen.SetRegistryRequest, error) {
	kind := input.Kind
	username := input.Username
	if existing != nil && !cmd.Flags().Changed("username") {
		username = existing.Username
	}
	if strings.TrimSpace(username) == "" {
		return gen.SetRegistryRequest{}, cerr.Usage("--username must be non-empty and is required when creating a registry")
	}
	// PUT replaces the registry settings: a rotation must carry over what the caller
	// did not restate — kind (the flag's default is `basic`, which would
	// silently downgrade a `ghcr` registry) and description alike.
	if existing != nil && !cmd.Flags().Changed("kind") && existing.Kind != "" {
		kind = string(existing.Kind)
	}
	wireKind := gen.SetRegistryRequestKind(kind)
	req := gen.SetRegistryRequest{Kind: &wireKind, Username: username, Password: input.Password}
	if existing != nil && !cmd.Flags().Changed("description") && existing.Description != nil {
		desc := *existing.Description
		req.Description = &desc
	}
	if cmd.Flags().Changed("description") {
		desc := input.Description
		req.Description = &desc
	}
	return req, nil
}

func btoi(v bool) int {
	if v {
		return 1
	}
	return 0
}

// printRegistry prints a registry credential's metadata: the API's JSON
// without any password field, or a table.
func printRegistry(a *app.App, raw api.Raw[gen.Registry]) error {
	raw = withoutWriteOnly(raw, writeOnlyFields.registry)
	resp := raw.V
	return a.Printer().Object(raw, [][2]string{{"host", resp.Host}, {"kind", string(resp.Kind)}, {"username", resp.Username}, {"updated_at", resp.UpdatedAt.Format(time.RFC3339)}})
}
