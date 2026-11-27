// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/workload"
)

// resolveSandboxArg resolves a sandbox id or name to its detail and the
// client of the region that serves it.
func resolveSandboxArg(ctx context.Context, a *app.App, ref string) (workload.ResolvedSandbox, error) {
	home, _, err := a.WorkloadClient(ctx, pickProject(a))
	if err != nil {
		return workload.ResolvedSandbox{}, err
	}
	return workload.ResolveSandbox(ctx, home, workload.NewCatalogCache(), ref)
}

// stringListFlag reads a comma-separated list flag in which "none" is the
// explicit empty list and an empty entry is refused.
func stringListFlag(name, raw string) ([]string, error) {
	if strings.TrimSpace(raw) == "none" {
		return []string{}, nil
	}
	parts := strings.Split(raw, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part == "" {
			return nil, cerr.Usage("--%s has an empty entry in %q", name, raw).WithHint("separate entries with commas, or pass none for an empty list")
		}
		out = append(out, part)
	}
	return out, nil
}

func newSandboxAccessTokenCommand() *cobra.Command {
	var operations, ports string
	ttl := &secondsFlag{}
	cmd := &cobra.Command{
		Use:   "access-token <sandbox>",
		Short: "Mint a scoped, short-lived access token for one sandbox",
		Long: "Mint an access token for one sandbox (POST /sandboxes/{id}/access-token) and print it. The token authorizes\n" +
			"the sandbox's services (toolbox, SSH, desktop, private previews) and its self-service routes for exactly the\n" +
			"granted operations and ports: hand it to a collaborator, a CI job or a browser instead of an API key. It cannot\n" +
			"be refreshed and cannot mint other credentials. The output carries the token: treat it as a secret.\n" +
			"--operations takes the sandbox-operation vocabulary (toolbox, toolbox:exec, toolbox:fs, toolbox:git, desktop,\n" +
			"ssh, sandbox:stop, sandbox:pause, sandbox:delete); omitted grants everything the caller may grant, and none\n" +
			"grants no operation (a ports-only token). --ports names the private-preview ports the token reaches; omitted\n" +
			"grants every port, none grants no port. Requires write:sandboxes.\n" +
			"Examples:\n  daytona sandbox access-token <sandbox> --operations ssh --ports none --ttl 10m\n" +
			"  daytona sandbox access-token <sandbox> --operations none --ports 3000,8080",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			body := gen.MintSandboxAccessTokenRequest{}
			if cmd.Flags().Changed("operations") {
				ops, err := stringListFlag("operations", operations)
				if err != nil {
					return err
				}
				body.Operations = &ops
			}
			if cmd.Flags().Changed("ports") {
				names, err := stringListFlag("ports", ports)
				if err != nil {
					return err
				}
				list := make([]int32, 0, len(names))
				for _, name := range names {
					port, err := parsePreviewPortArg(name)
					if err != nil {
						return err
					}
					list = append(list, int32(port))
				}
				body.Ports = &list
			}
			if ttl.set {
				seconds := ttl.seconds
				body.TtlSeconds = &seconds
			}
			resolved, err := resolveSandboxArg(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			var token api.Raw[gen.SandboxAccessToken]
			if err := resolved.Client.Do(cmd.Context(), http.MethodPost, "/sandboxes/"+url.PathEscape(resolved.Detail.Id)+"/access-token", nil, body, &token); err != nil {
				return err
			}
			if err := checkAccessTokenResponse(token); err != nil {
				return err
			}
			portCell := "all"
			if token.V.Ports != nil {
				cells := make([]string, 0, len(*token.V.Ports))
				for _, port := range *token.V.Ports {
					cells = append(cells, strconv.Itoa(int(port)))
				}
				portCell = strings.Join(cells, ",")
			}
			return a.Printer().Object(token, [][2]string{
				{"access_token", token.V.AccessToken},
				{"token_type", token.V.TokenType},
				{"expires_at", token.V.ExpiresAt.Format(time.RFC3339)},
				{"operations", strings.Join(token.V.Operations, ",")},
				{"ports", portCell},
			})
		},
	}
	cmd.Flags().StringVar(&operations, "operations", "", "operations to grant, comma-separated, or none (default: everything the caller may grant)")
	cmd.Flags().StringVar(&ports, "ports", "", "private-preview ports the token may reach, comma-separated, or none (default: every port)")
	addSecondsFlag(cmd.Flags(), ttl, "ttl", "", "token lifetime (Go duration in whole seconds; default: the platform's, 1h unless configured; the platform caps it)")
	return cmd
}

// checkAccessTokenResponse refuses a mint response that breaks the contract
// (a required field missing or empty, a port below zero) instead of printing
// a token that cannot be used. The message names the field, never the token.
func checkAccessTokenResponse(token api.Raw[gen.SandboxAccessToken]) error {
	malformed := func(detail string) error {
		return cerr.New(cerr.ExitFailure, "the access token response %s", detail)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(token.JSON, &fields); err != nil || fields == nil {
		return malformed("is not a JSON object")
	}
	for _, name := range []string{"access_token", "token_type", "expires_at", "operations"} {
		if v, ok := fields[name]; !ok || string(v) == "null" {
			return malformed("has no " + name)
		}
	}
	switch {
	case token.V.AccessToken == "":
		return malformed("has an empty access_token")
	case token.V.TokenType == "":
		return malformed("has an empty token_type")
	case token.V.ExpiresAt.IsZero():
		return malformed("has no valid expires_at")
	}
	if token.V.Ports != nil {
		for _, port := range *token.V.Ports {
			if port < 0 {
				return malformed(fmt.Sprintf("grants port %d, below zero", port))
			}
		}
	}
	return nil
}

// withETag is a response body with the ETag the next conditional update
// sends as If-Match. The ETag header is the value If-Match needs, so it
// takes the "etag" key even if a later API adds a body field of that name.
func withETag(raw json.RawMessage, etag string) (map[string]json.RawMessage, error) {
	out := map[string]json.RawMessage{}
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	encoded, err := json.Marshal(etag)
	if err != nil {
		return nil, err
	}
	out["etag"] = encoded
	return out, nil
}

// conditionalPut sends a full replacement with If-Match: the given --if-match,
// or else the ETag of a fresh read, so a change made between that read and
// this write is refused (412) instead of overwritten.
func conditionalPut(ctx context.Context, client *api.Client, path, ifMatch string, body any) (json.RawMessage, string, error) {
	if ifMatch == "" {
		headers, err := client.DoReturningHeaders(ctx, http.MethodGet, path, nil, nil, nil, nil)
		if err != nil {
			return nil, "", err
		}
		ifMatch = headers.Get("ETag")
		if ifMatch == "" {
			return nil, "", cerr.New(cerr.ExitFailure, "GET %s returned no ETag to update with", path)
		}
	}
	var raw json.RawMessage
	headers, err := client.DoReturningHeaders(ctx, http.MethodPut, path, nil, http.Header{"If-Match": {ifMatch}}, body, &raw)
	if err != nil {
		return nil, "", err
	}
	return raw, headers.Get("ETag"), nil
}

func addIfMatchFlag(cmd *cobra.Command, target *string, what string) {
	cmd.Flags().StringVar(target, "if-match", "", "the etag of the "+what+" you read (from `get`): the update is refused with 412 if it changed since; omitted, the current etag is read just before the write, so the command replaces whatever is current")
}

func checkIfMatch(cmd *cobra.Command, ifMatch string) error {
	if cmd.Flags().Changed("if-match") && strings.TrimSpace(ifMatch) == "" {
		return cerr.Usage("--if-match is empty").WithHint("pass the etag printed by `get`, or omit --if-match")
	}
	return nil
}

func newSandboxSecretsSubcommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "secrets",
		Short: "Read and replace the project secrets attached to a sandbox",
		Long: "Read and replace the project secrets attached to a running sandbox, by name; values are never sent or printed.\n" +
			"A change applies asynchronously: `get` shows status pending until the sandbox has applied desired_revision.\n" +
			"Running processes keep their environment; processes started afterwards see the new set.",
	}
	cmd.AddCommand(newSandboxSecretsGetCommand(), newSandboxSecretsSetCommand())
	return cmd
}

func sandboxSecretsKV(raw json.RawMessage, etag string) [][2]string {
	var v gen.SandboxSecrets
	_ = json.Unmarshal(raw, &v)
	return [][2]string{
		{"secrets", strings.Join(v.Secrets, ",")},
		{"status", formatPtr(v.Status)},
		{"desired_revision", strconv.FormatInt(v.DesiredRevision, 10)},
		{"applied_revision", formatInt64Ptr(v.AppliedRevision)},
		{"error", formatPtr(v.Error)},
		{"etag", etag},
	}
}

func formatInt64Ptr(v *int64) string {
	if v == nil {
		return ""
	}
	return strconv.FormatInt(*v, 10)
}

func newSandboxSecretsGetCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "get <sandbox>",
		Short: "Show the secrets attached to a sandbox and whether they are applied",
		Long:  "Show the names of the project secrets attached to a sandbox, how far the sandbox has applied them, and the etag `set --if-match` takes. Requires read:sandboxes and read:secrets.",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			resolved, err := resolveSandboxArg(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			var raw json.RawMessage
			headers, err := resolved.Client.DoReturningHeaders(cmd.Context(), http.MethodGet, "/sandboxes/"+url.PathEscape(resolved.Detail.Id)+"/secrets", nil, nil, nil, &raw)
			if err != nil {
				return err
			}
			etag := headers.Get("ETag")
			out, err := withETag(raw, etag)
			if err != nil {
				return err
			}
			return a.Printer().Object(out, sandboxSecretsKV(raw, etag))
		},
	}
}

func newSandboxSecretsSetCommand() *cobra.Command {
	var names []string
	var clear bool
	var ifMatch string
	cmd := &cobra.Command{
		Use:   "set <sandbox>",
		Short: "Replace the set of secrets attached to a sandbox",
		Long: "Replace the whole set of project secrets attached to a sandbox: names left out are detached, --clear detaches\n" +
			"every one. Unchanged names stay attached. Requires write:sandboxes and read:secrets.\n" +
			"Examples:\n  daytona sandbox secrets set <sandbox> --secret GITHUB_TOKEN --secret NPM_TOKEN\n" +
			"  daytona sandbox secrets set <sandbox> --clear --if-match <etag>",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkIfMatch(cmd, ifMatch); err != nil {
				return err
			}
			if err := mustOneOf(btoi(cmd.Flags().Changed("secret"))+btoi(clear), "--secret", "--clear"); err != nil {
				return err
			}
			body := gen.SetSandboxSecretsRequest{Secrets: []string{}}
			for _, name := range names {
				name = strings.TrimSpace(name)
				if name == "" {
					return cerr.Usage("--secret is empty").WithHint("pass a project secret name, or --clear to detach every secret")
				}
				body.Secrets = append(body.Secrets, name)
			}
			a := FromContext(cmd.Context())
			resolved, err := resolveSandboxArg(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			raw, etag, err := conditionalPut(cmd.Context(), resolved.Client, "/sandboxes/"+url.PathEscape(resolved.Detail.Id)+"/secrets", ifMatch, body)
			if err != nil {
				return err
			}
			out, err := withETag(raw, etag)
			if err != nil {
				return err
			}
			return a.Printer().Object(out, sandboxSecretsKV(raw, etag))
		},
	}
	cmd.Flags().StringArrayVar(&names, "secret", nil, "project secret name to attach (repeatable); the list replaces the current one")
	cmd.Flags().BoolVar(&clear, "clear", false, "detach every secret")
	addIfMatchFlag(cmd, &ifMatch, "secrets")
	return cmd
}

func newSandboxNetworkPolicySubcommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "network-policy",
		Short: "Read, replace and reset a sandbox's egress network policy",
		Long: "Read, replace and reset the egress network policy of a sandbox. A change applies asynchronously: `get` shows\n" +
			"the desired and the applied rules and their revisions.",
	}
	cmd.AddCommand(newSandboxNetworkPolicyGetCommand(), newSandboxNetworkPolicySetCommand(), newSandboxNetworkPolicyResetCommand())
	return cmd
}

func networkPolicyKV(raw json.RawMessage, etag string) [][2]string {
	var v gen.NetworkPolicy
	_ = json.Unmarshal(raw, &v)
	rules := func(r *gen.NetworkPolicyRules) string {
		if r == nil {
			return "open"
		}
		b, _ := json.Marshal(r)
		return string(b)
	}
	return [][2]string{
		{"desired", rules(v.Desired)},
		{"applied", rules(v.Applied)},
		{"status", formatPtr(v.Status)},
		{"desired_revision", strconv.FormatInt(v.DesiredRevision, 10)},
		{"applied_revision", formatInt64Ptr(v.AppliedRevision)},
		{"error", formatPtr(v.Error)},
		{"etag", etag},
	}
}

func newSandboxNetworkPolicyGetCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "get <sandbox>",
		Short: "Show a sandbox's network policy",
		Long:  "Show a sandbox's requested and applied network policy (null rules: open network access) and the etag `set` and `reset` take as --if-match. Requires read:sandboxes.",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			resolved, err := resolveSandboxArg(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			var raw json.RawMessage
			headers, err := resolved.Client.DoReturningHeaders(cmd.Context(), http.MethodGet, "/sandboxes/"+url.PathEscape(resolved.Detail.Id)+"/network-policy", nil, nil, nil, &raw)
			if err != nil {
				return err
			}
			etag := headers.Get("ETag")
			out, err := withETag(raw, etag)
			if err != nil {
				return err
			}
			return a.Printer().Object(out, networkPolicyKV(raw, etag))
		},
	}
}

func newSandboxNetworkPolicySetCommand() *cobra.Command {
	var allowDomains, allowCIDRs []string
	var blockAll bool
	var ifMatch string
	cmd := &cobra.Command{
		Use:   "set <sandbox>",
		Short: "Replace a sandbox's network policy",
		Long: "Replace a sandbox's whole network policy, with the same flags as `sandbox create`. A domain list implies\n" +
			"--block-all; --allow-cidr needs --block-all or --allow-domain. `reset` returns the sandbox to open network\n" +
			"access. Requires write:sandboxes.\n" +
			"Examples:\n  daytona sandbox network-policy set <sandbox> --allow-domain pypi.org --allow-domain '*.github.com'\n" +
			"  daytona sandbox network-policy set <sandbox> --block-all --allow-cidr 10.0.0.0/8",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkIfMatch(cmd, ifMatch); err != nil {
				return err
			}
			changed := cmd.Flags().Changed
			if !changed("allow-domain") && !changed("allow-cidr") && !changed("block-all") {
				return cerr.Usage("network-policy set needs --allow-domain, --allow-cidr or --block-all").
					WithHint("run `daytona sandbox network-policy reset` for open network access")
			}
			if changed("allow-domain") && changed("block-all") && !blockAll {
				return cerr.Usage("--allow-domain implies --block-all; --block-all=false is rejected (NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL)")
			}
			if changed("allow-cidr") && !blockAll && !changed("allow-domain") {
				return cerr.Usage("--allow-cidr restricts egress only with --block-all or --allow-domain; without them the policy is open")
			}
			body := gen.SetSandboxNetworkPolicyRequest{}
			if changed("allow-domain") {
				body.AllowDomains = &allowDomains
			}
			if changed("allow-cidr") {
				body.AllowCidrs = &allowCIDRs
			}
			if changed("block-all") {
				body.BlockAll = &blockAll
			}
			a := FromContext(cmd.Context())
			resolved, err := resolveSandboxArg(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			raw, etag, err := conditionalPut(cmd.Context(), resolved.Client, "/sandboxes/"+url.PathEscape(resolved.Detail.Id)+"/network-policy", ifMatch, body)
			if err != nil {
				return err
			}
			out, err := withETag(raw, etag)
			if err != nil {
				return err
			}
			return a.Printer().Object(out, networkPolicyKV(raw, etag))
		},
	}
	cmd.Flags().StringArrayVar(&allowDomains, "allow-domain", nil, "allowed hostname or *.wildcard (repeatable; implies --block-all)")
	cmd.Flags().StringArrayVar(&allowCIDRs, "allow-cidr", nil, "allowed IPv4 address or CIDR, any port (repeatable; needs --block-all or --allow-domain)")
	cmd.Flags().BoolVar(&blockAll, "block-all", false, "deny all egress not explicitly allowed")
	addIfMatchFlag(cmd, &ifMatch, "policy")
	return cmd
}

func newSandboxNetworkPolicyResetCommand() *cobra.Command {
	var ifMatch string
	cmd := &cobra.Command{
		Use:   "reset <sandbox>",
		Short: "Remove a sandbox's network policy (open network access)",
		Long:  "Remove a sandbox's network policy, returning it to open network access. Requires write:sandboxes.\nExample:\n  daytona sandbox network-policy reset <sandbox>",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkIfMatch(cmd, ifMatch); err != nil {
				return err
			}
			a := FromContext(cmd.Context())
			resolved, err := resolveSandboxArg(cmd.Context(), a, args[0])
			if err != nil {
				return err
			}
			raw, etag, err := conditionalPut(cmd.Context(), resolved.Client, "/sandboxes/"+url.PathEscape(resolved.Detail.Id)+"/network-policy", ifMatch, json.RawMessage("null"))
			if err != nil {
				return err
			}
			out, err := withETag(raw, etag)
			if err != nil {
				return err
			}
			return a.Printer().Object(out, networkPolicyKV(raw, etag))
		},
	}
	addIfMatchFlag(cmd, &ifMatch, "policy")
	return cmd
}
