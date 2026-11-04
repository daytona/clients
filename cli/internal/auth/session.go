// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"net/http"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/mgmt"
)

const refreshSkew = 60 * time.Second

// ServerConfig is the generated GET /config bootstrap shape.
type ServerConfig = gen.Config

func FetchServerConfig(ctx context.Context, c *api.Client) (*ServerConfig, error) {
	var sc ServerConfig
	if err := c.Do(ctx, http.MethodGet, "/config", nil, nil, &sc); err != nil {
		return nil, err
	}
	return &sc, nil
}

// ProviderFor resolves the OIDC client configured for CLI login.
func ProviderFor(ctx context.Context, hc *http.Client, sc *ServerConfig, override string, getenv func(string) string) (*Provider, error) {
	if sc.Oidc == nil {
		return nil, cerr.New(cerr.ExitUsage, "this deployment does not support CLI login").
			WithHint("use --api-key or set DAYTONA_API_KEY")
	}
	clientID := override
	if clientID == "" {
		clientID = getenv("DAYTONA_OIDC_CLIENT_ID")
	}
	if clientID == "" && sc.Oidc.CliClientId != nil {
		clientID = *sc.Oidc.CliClientId
	}
	if clientID == "" {
		return nil, cerr.New(cerr.ExitUsage, "this deployment does not support CLI login").
			WithHint("use --api-key or set DAYTONA_API_KEY")
	}
	p, err := Discover(ctx, hc, sc.Oidc.Issuer)
	if err != nil {
		return nil, err
	}
	p.ClientID = clientID
	if sc.Oidc.Audience != nil {
		p.Audience = *sc.Oidc.Audience
	}
	p.Scope = sc.Oidc.Scope
	p.AuthKit = sc.Oidc.Provider == gen.Workos
	if p.AuthKit && p.DeviceAuthorizationEndpoint == "" {
		p.DeviceAuthorizationEndpoint = strings.TrimRight(p.AuthorizationEndpoint, "/") + "/device"
	}
	return p, nil
}

// Install preserves explicit credential precedence through SetCredentialResolver.
func Install(a *app.App) {
	creds, err := a.Store.LoadCredentials()
	if err != nil {
		return
	}
	pc, ok := creds.Profiles[a.Profile.Name]
	if !ok {
		return
	}
	switch {
	case pc.Token != "":
		tok := pc.Token
		a.SetCredentialResolver("profile-token", func(context.Context) (string, error) { return tok, nil })
	case pc.OIDC != nil:
		a.SetCredentialResolver("profile-oidc", func(ctx context.Context) (string, error) { return AccessToken(ctx, a) })
	}
}

// InstallProjectResolver resolves names for every credential source. Static
// credentials that cannot list projects leave the target route authoritative.
func InstallProjectResolver(a *app.App) {
	a.ProjectResolver = func(ctx context.Context, orgID, ref string) (string, error) {
		mc, err := a.Client(ctx)
		if err != nil {
			return "", err
		}
		projects, err := mgmt.ListProjects(ctx, mc, orgID)
		if err != nil {
			code := cerr.ExitCode(err)
			static := a.HasExplicitCredential() || a.CredentialSource == "profile-token"
			if static && (code == cerr.ExitAuth || code == cerr.ExitForbidden || code == cerr.ExitNotFound) {
				return ref, nil
			}
			return "", err
		}
		project, err := mgmt.ResolveProject(projects, ref)
		if err != nil {
			return "", err
		}
		return project.Id.String(), nil
	}
}
