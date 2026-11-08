// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/spf13/cobra"
)

func init() {
	Register(newSecretCmd)
}

func newSecretCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "secret", Short: "Project secrets: set, list, get, delete", GroupID: GroupManagement}
	cmd.AddCommand(newSecretSetCmd(), newSecretListCmd(), newSecretGetCmd(), newSecretDeleteCmd())
	return cmd
}

func newSecretSetCmd() *cobra.Command {
	var value, fromFile string
	var useStdin bool
	var allowedDomains []string
	var description string
	cmd := &cobra.Command{Use: "set <name>", Short: "Create or update a project secret", Long: "Create or update a project secret and print it (never its value). It reads the stored secret first, so options you do not pass keep their values: it needs read as well as write access to secrets.", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		count := 0
		if cmd.Flags().Changed("value") {
			count++
		}
		if cmd.Flags().Changed("from-file") {
			count++
		}
		if useStdin {
			count++
		}
		if err := mustOneOf(count, "--value", "--from-file", "--stdin"); err != nil {
			return err
		}
		if cmd.Flags().Changed("from-file") && strings.TrimSpace(fromFile) == "" {
			return cerr.Usage("--from-file is empty; pass the path of the file that holds the value")
		}
		secretValue, err := secretInputValue(value, fromFile, cmd.Flags().Changed("from-file"), useStdin)
		if err != nil {
			return err
		}
		// An empty value is never meant: it usually comes from an unset
		// variable or an empty file, and would overwrite the stored value,
		// which cannot be read back.
		if secretValue == "" {
			return cerr.Usage("the secret value is empty").
				WithHint("pass the value with --value, --from-file or --stdin; `daytona secret delete` removes a secret")
		}
		if cmd.Flags().Changed("value") {
			FromContext(cmd.Context()).Info("note: --value puts the secret in the process list and shell history; prefer --stdin or --from-file")
		}
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		client, _, err := a.WorkloadClient(ctx, pickProject(a))
		if err != nil {
			return err
		}
		path := "/secrets/" + url.PathEscape(args[0])
		var existing gen.Secret
		err = client.Do(ctx, http.MethodGet, path, nil, nil, &existing)
		if err != nil && cerr.ExitCode(err) != cerr.ExitNotFound {
			return err
		}
		var current *gen.Secret
		if err == nil {
			current = &existing
		}
		putReq, err := secretPutReq(cmd, current, secretSetInput{
			Value: secretValue, AllowedDomains: allowedDomains, Description: description,
		})
		if err != nil {
			return err
		}
		var stored api.Raw[gen.Secret]
		if err := client.Do(ctx, http.MethodPut, path, nil, putReq, &stored); err != nil {
			return err
		}
		return printSecret(a, stored)
	}}
	cmd.Flags().StringVar(&value, "value", "", "secret value on the command line — visible to other local users via the process list and shell history; prefer --stdin or --from-file")
	cmd.Flags().StringVar(&fromFile, "from-file", "", "read the secret value from a file; strips exactly one trailing newline (or CRLF)")
	cmd.Flags().BoolVar(&useStdin, "stdin", false, "read the secret value from stdin; strips exactly one trailing newline (or CRLF)")
	cmd.Flags().StringSliceVar(&allowedDomains, "allowed-domain", nil, "hosts where this secret's value may be sent (exact or *.wildcard), or '*' for any verified host; '*' lets the value reach any HTTPS host including one a compromised sandbox controls")
	cmd.Flags().StringVar(&description, "description", "", "secret description (set to an empty string to clear it)")
	return cmd
}

func newSecretListCmd() *cobra.Command {
	var flags pageFlags
	cmd := &cobra.Command{Use: "list", Short: "List project secrets (metadata only)", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		client, _, err := a.WorkloadClient(ctx, pickProject(a))
		if err != nil {
			return err
		}
		fetch := func(ctx context.Context, cursor string) (output.List[api.Raw[gen.Secret]], error) {
			return getRawList[gen.Secret](ctx, client, "/secrets", listQuery(flags, cursor), "secrets")
		}
		var items output.List[api.Raw[gen.Secret]]
		if flags.all {
			items, err = collectPaged(ctx, flags.cursor, fetch)
		} else {
			items, err = fetch(ctx, flags.cursor)
		}
		if err != nil {
			return err
		}
		return listOutRaw(a.Printer(), items, func(rows []gen.Secret) output.Table {
			tableRows := make([][]string, 0, len(rows))
			for _, row := range rows {
				tableRows = append(tableRows, []string{row.Name, joinComma(row.AllowedDomains), row.UpdatedAt.Format(time.RFC3339)})
			}
			return output.Table{Headers: []string{"NAME", "ALLOWED_DOMAINS", "UPDATED_AT"}, Rows: tableRows}
		})
	}}
	addPageFlags(cmd, &flags)
	return cmd
}

func newSecretGetCmd() *cobra.Command {
	return &cobra.Command{Use: "get <name>", Short: "Show one project's secret metadata", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		a := FromContext(cmd.Context())
		ctx := cmd.Context()
		client, _, err := a.WorkloadClient(ctx, pickProject(a))
		if err != nil {
			return err
		}
		var raw api.Raw[gen.Secret]
		if err := client.Do(ctx, http.MethodGet, "/secrets/"+url.PathEscape(args[0]), nil, nil, &raw); err != nil {
			return err
		}
		a.Info("Secret values are write-only and are never returned by the API.")
		return printSecret(a, raw)
	}}
}

func newSecretDeleteCmd() *cobra.Command {
	return &cobra.Command{Use: "delete <name>", Short: "Delete a project secret", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		a := FromContext(cmd.Context())
		if err := a.Confirm("delete", "secret "+args[0]); err != nil {
			return err
		}
		ctx := cmd.Context()
		client, _, err := a.WorkloadClient(ctx, pickProject(a))
		if err != nil {
			return err
		}
		if err := client.Do(ctx, http.MethodDelete, "/secrets/"+url.PathEscape(args[0]), nil, nil, nil); err != nil {
			return err
		}
		return printAck(a, "name", args[0], "deleted", "")
	}}
}

type secretSetInput struct {
	Value          string
	AllowedDomains []string
	Description    string
}

func secretPutReq(cmd *cobra.Command, existing *gen.Secret, input secretSetInput) (gen.SetSecretRequest, error) {
	req := gen.SetSecretRequest{Value: input.Value}
	isCreate := existing == nil

	// An explicitly empty flag (--allowed-domain '' from an unset shell
	// variable) counts as no scope, so it gets the usage error below instead of
	// sending [] for the API to reject.
	given := make([]string, 0, len(input.AllowedDomains))
	for _, d := range input.AllowedDomains {
		if d = strings.TrimSpace(d); d != "" {
			given = append(given, d)
		}
	}

	if len(given) > 0 {
		req.AllowedDomains = &given
	} else if isCreate {
		return gen.SetSecretRequest{}, cerr.New(cerr.ExitUsage,
			"--allowed-domain is required when creating a secret; use --allowed-domain '*' to allow any verified host")
	} else if len(existing.AllowedDomains) == 0 {
		return gen.SetSecretRequest{}, cerr.New(cerr.ExitUsage,
			"--allowed-domain is required: this secret has no scope; use --allowed-domain '*' to allow any verified host")
	} else {
		domains := append([]string{}, existing.AllowedDomains...)
		req.AllowedDomains = &domains
	}

	if !isCreate && !cmd.Flags().Changed("description") && existing.Description != nil {
		desc := *existing.Description
		req.Description = &desc
	}
	if cmd.Flags().Changed("description") {
		desc := input.Description
		req.Description = &desc
	}
	return req, nil
}

// secretInputValue reads the value from the one source the caller selected
// (by flag presence, never by the flag's content).
func secretInputValue(value, fromFile string, fromFileSet, useStdin bool) (string, error) {
	if fromFileSet {
		body, err := os.ReadFile(fromFile)
		if err != nil {
			return "", cerr.Wrap(cerr.ExitFailure, err, "read %s: %v", fromFile, err)
		}
		return trimOneTrailingLineEnding(string(body)), nil
	}
	if useStdin {
		body, err := io.ReadAll(os.Stdin)
		if err != nil {
			return "", cerr.Wrap(cerr.ExitFailure, err, "read stdin: %v", err)
		}
		return trimOneTrailingLineEnding(string(body)), nil
	}
	return value, nil
}

func trimOneTrailingLineEnding(value string) string {
	if strings.HasSuffix(value, "\r\n") {
		return strings.TrimSuffix(value, "\r\n")
	}
	return strings.TrimSuffix(value, "\n")
}

// printSecret prints a secret's metadata: the API's JSON, or a table.
func printSecret(a *app.App, raw api.Raw[gen.Secret]) error {
	resp := raw.V
	return a.Printer().Object(raw, [][2]string{{"name", resp.Name}, {"allowed_domains", joinComma(resp.AllowedDomains)}, {"updated_at", resp.UpdatedAt.Format(time.RFC3339)}})
}
