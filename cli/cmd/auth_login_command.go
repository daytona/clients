// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"strings"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/spf13/cobra"
)

func newLoginCmd() *cobra.Command {
	var device, browser bool
	var token, clientID string
	c := &cobra.Command{
		Use: "login", Short: "Sign in with your browser, a device code, or a static credential",
		Long: `Signs in to the Daytona API (` + app.DefaultAPIURL + `) and saves the session in
the active profile. For a test environment, point the CLI at another API with
DAYTONA_V2_API_URL (or --api-url); the profile remembers it.
--profile NAME creates a named profile: 1–64 ASCII letters, digits, '.', '_'
or '-', excluding '.' and '..'.

Default at a terminal: opens your browser (authorization code + PKCE on a
loopback redirect) and prints the URL too — nothing to type, approve and you
are done. Without a terminal (agents, CI) or inside an SSH session (where a
browser on your machine cannot reach this host's loopback redirect) the CLI
switches to the device flow automatically; --device forces it — you open a URL
on any device and enter a short code.
With JSON/YAML output or non-terminal stdout, device login prints one JSON
event with known verification_uri, verification_uri_complete, user_code and
expires_in fields before waiting. It prints no additional result on stdout.
--token: stores a static credential (a project API key, an org API key, or any
bearer the server accepts) as-is. The CLI only rejects an empty value (for
example an unset CI secret) and never inspects it otherwise; the server decides
what it may do.

After a browser or device login the CLI selects your personal organization
and its default project the first time, and remembers your last selection
afterwards. Sandbox commands use a short-lived platform token where supported;
toolbox access and other deployments use a CLI-minted project API key with a
24-hour expiry. Replacement attempts to revoke superseded keys; logout attempts
to revoke cached login-minted keys.

Examples:
  daytona login
  daytona login --device
  daytona login --token dtn_…`,
		GroupID: GroupAuth, Args: cobra.NoArgs,
		Annotations: map[string]string{annotationCreatesProfile: "true"},
		RunE: func(cmd *cobra.Command, _ []string) error {
			a := FromContext(cmd.Context())
			name := a.Profile.Name
			if cmd.Flags().Changed("profile") {
				name = a.Globals.Profile
			}
			if err := config.ValidateProfileName(name); err != nil {
				return err
			}
			if cmd.Flags().Changed("token") {
				if strings.TrimSpace(token) == "" {
					return cerr.Usage("--token is empty; pass the credential value, or omit --token to sign in interactively")
				}
				return loginToken(cmd.Context(), a, token)
			}
			return loginOIDC(cmd.Context(), a, device, browser, clientID)
		},
	}
	c.Flags().BoolVar(&device, "device", false, "device-code login for hosts without a browser")
	c.Flags().BoolVar(&browser, "browser", false, "force the browser (loopback) flow even inside an SSH session, for when you forward the callback port with ssh -L")
	c.Flags().StringVar(&token, "token", "", "store this static credential instead of signing in")
	c.Flags().StringVar(&clientID, "client-id", "", "OIDC client id override (default: discovered from the API, or DAYTONA_OIDC_CLIENT_ID)")
	return c
}

func loginToken(ctx context.Context, a *app.App, token string) error {
	base, name := a.APIURL(), a.Profile.Name
	err := a.Store.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		p := config.Profile{Name: name, APIURL: base, Credential: "token"}
		if cur, ok := c.Profile(name); ok {
			p.OIDCClientID = cur.OIDCClientID
		}
		if err := c.Upsert(p); err != nil {
			return false, false, err
		}
		if c.ActiveProfile == "" {
			c.ActiveProfile = name
		}
		cr.Profiles[name] = config.ProfileCredentials{Token: token}
		return true, true, nil
	})
	if err != nil {
		return err
	}
	a.Info("Saved credential in profile %q for %s.", name, base)
	return nil
}
