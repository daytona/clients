// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package app

import (
	"context"
	"fmt"
	"strings"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
)

func staticCred(k string) func(context.Context) (string, error) {
	return func(context.Context) (string, error) { return k, nil }
}

// SetCredentialResolver installs the profile-credential resolver (auth pkg)
// unless an explicit credential already won.
func (a *App) SetCredentialResolver(source string, fn func(ctx context.Context) (string, error)) {
	if a.credentialFn != nil {
		return
	}
	a.CredentialSource = source
	a.credentialFn = fn
	a.savedCredential = true
}

// credentialURLError refuses the profile's saved credential when this
// invocation's API URL is not the one it was issued for: it is never sent,
// refreshed or exchanged at another URL. An explicit credential (--api-key /
// DAYTONA_API_KEY) is the way to call another API.
func (a *App) credentialURLError() error {
	if !a.savedCredential || sameAPIURL(a.APIURL(), a.savedAPIURL) {
		return nil
	}
	return cerr.Usage("profile %q's saved credential was issued for %s; it is not sent to %s", a.Profile.Name, a.savedAPIURL, a.APIURL()).
		WithHint("pass --api-key / DAYTONA_API_KEY for that API, sign in there with `daytona login --api-url %s --profile NAME`, or drop --api-url / DAYTONA_V2_API_URL", a.APIURL())
}

// ReplaceCredentialResolver installs a resolver unconditionally. Used only by
// `login`, whose whole purpose is to change the credential for the rest of
// the invocation (the context-setup calls that follow must use the tokens
// just saved, not a previously stored static credential).
func (a *App) ReplaceCredentialResolver(source string, fn func(ctx context.Context) (string, error)) {
	a.CredentialSource = source
	a.credentialFn = fn
	a.savedCredential = true
	a.savedAPIURL = a.APIURL()
}

// HasExplicitCredential reports whether --api-key / DAYTONA_API_KEY was given.
func (a *App) HasExplicitCredential() bool {
	return a.CredentialSource == "flag" || a.CredentialSource == "env"
}

// Credential returns the bearer for this invocation.
func (a *App) Credential(ctx context.Context) (string, error) {
	if err := a.credentialURLError(); err != nil {
		return "", err
	}
	if a.credentialFn == nil {
		return "", cerr.New(cerr.ExitAuth, "not logged in").
			WithHint("run `daytona login`, or pass --api-key / set DAYTONA_API_KEY")
	}
	return a.credentialFn(ctx)
}

// Client returns an authenticated API client against the profile's base URL.
func (a *App) Client(ctx context.Context) (*api.Client, error) {
	base := a.APIURL()
	cred, err := a.Credential(ctx)
	if err != nil {
		return nil, err
	}
	c := api.New(base, cred, Version)
	if a.Globals.Debug {
		c.Debug = func(format string, args ...any) { fmt.Fprintf(a.Stderr, "debug: "+format+"\n", args...) }
	}
	return c, nil
}

// WorkloadClient returns a client for sandbox/toolbox routes of the selected
// project: the explicit/static credential as-is, or the OIDC profile's
// cached/minted project key. `pick` is the interactive project picker.
func (a *App) WorkloadClient(ctx context.Context, pick func(ctx context.Context) (string, error)) (*api.Client, string, error) {
	base := a.APIURL()
	if err := a.credentialURLError(); err != nil {
		return nil, "", err
	}
	if a.HasExplicitCredential() || a.CredentialSource == "profile-token" {
		// A static credential is sent as-is on every route, and the server's
		// answer stands: the CLI never mints, caches or substitutes another
		// key for it, and never falls back to a saved login. Usually it IS a
		// project key, bound server-side to its project, so no project
		// selection is required.
		cred, err := a.Credential(ctx)
		if err != nil {
			return nil, "", err
		}
		project, err := a.resolveSelectedProject(ctx)
		if err != nil {
			return nil, "", err
		}
		if err := a.checkStaticKeyProject(ctx, project); err != nil {
			return nil, "", err
		}
		return a.newClient(base, cred), project, nil
	}
	// "Not logged in" is the root cause when there is no credential at all;
	// report it (exit 3) before asking for a project (exit 2), so an agent
	// gets the actionable diagnosis first.
	if a.CredentialSource == "" || a.WorkloadCredentialFn == nil {
		return nil, "", cerr.New(cerr.ExitAuth, "not logged in").WithHint("run `daytona login`, or pass --api-key / set DAYTONA_API_KEY")
	}
	project, err := a.RequireProject(ctx, pick)
	if err != nil {
		return nil, "", err
	}
	// Platform token first: on an issuer
	// deployment the login's JWT is exchanged for a 300 s project-scoped
	// token — the same bearer the dashboard uses — instead of minting a
	// long-lived key. A 401 on it (expired between check and use, or the
	// signing key rotated) drops it and exchanges once more; a 403 is a
	// permission answer under the caller's own effective permissions and
	// stands. Not an issuer → the key path below.
	if a.PlatformTokenFn != nil {
		tok, ok, err := a.PlatformTokenFn(ctx, project, "")
		if err != nil {
			return nil, "", err
		}
		if ok {
			c := a.newClient(base, tok)
			c.OnUnauthorized = func(ctx context.Context) (string, error) {
				fresh, _, err := a.PlatformTokenFn(ctx, project, tok)
				return fresh, err
			}
			return c, project, nil
		}
	}
	return a.workloadKeyClient(ctx, base, project)
}

// StaticKeyProjectContext describes the static credential's project once per
// invocation (ok=false when it is not a project API key, or no describer is
// installed).
func (a *App) StaticKeyProjectContext(ctx context.Context) (StaticKeyProject, bool, error) {
	if a.StaticKeyProjectFn == nil {
		return StaticKeyProject{}, false, nil
	}
	if a.staticKeyProject == nil {
		project, ok, err := a.StaticKeyProjectFn(ctx)
		a.staticKeyProject = &staticKeyProjectResult{project: project, ok: ok, err: err}
	}
	r := a.staticKeyProject
	return r.project, r.ok, r.err
}

// checkStaticKeyProject refuses a --project / DAYTONA_PROJECT that names
// another project than the one a project API key acts in: the server would
// act in the key's project whatever the selection says.
func (a *App) checkStaticKeyProject(ctx context.Context, selected string) error {
	if !a.projectFromOverride || selected == "" {
		return nil
	}
	key, ok, err := a.StaticKeyProjectContext(ctx)
	if err != nil || !ok {
		return err
	}
	if strings.EqualFold(selected, key.ID) || strings.EqualFold(selected, key.Name) {
		return nil
	}
	return cerr.Usage("--project %q is not the project this API key acts in (%s, %s)", selected, key.Name, key.ID).
		WithHint("a project API key always acts in its own project: drop --project / DAYTONA_PROJECT, or use a key of project %s", selected)
}

// workloadKeyClient returns a client that uses a project key for the
// selected project; the CLI sends toolbox and sandbox requests with it when
// it cannot use the login's platform token.
func (a *App) workloadKeyClient(ctx context.Context, base, project string) (*api.Client, string, error) {
	// Cache first (no GET /orgs or key-mint request): use an unexpired key
	// the login minted earlier. Its
	// ceiling may be narrower than the org's CURRENT ceiling — widening
	// never fails a call that was already allowed, and a call the wider
	// ceiling would newly allow answers 403, which routes back here to drop
	// the key and mint under the current ceiling (one retry, then the 403
	// stands). The key expires with the JWT, bounding the drift.
	if a.CachedWorkloadKeyFn != nil {
		if cached := a.CachedWorkloadKeyFn(project); cached != "" {
			c := a.newClient(base, cached)
			c.RetryForbidden = true
			c.OnUnauthorized = func(ctx context.Context) (string, error) {
				return a.WorkloadCredentialFn(ctx, project, cached)
			}
			return c, project, nil
		}
	}
	cred, err := a.WorkloadCredentialFn(ctx, project, "")
	if err != nil {
		return nil, "", err
	}
	// A fresh key is authoritative: a 401/403 on it is an error, not a
	// re-mint loop.
	return a.newClient(base, cred), project, nil
}

// SigningClient returns the client that mints pre-signed toolbox file URLs
// (`POST /sandboxes/{id}/files/sign`) for `sandbox cp`: the same
// credential rule as ToolboxClient — the explicit or profile credential as
// is, else a project key minted under the login — against the API base URL.
// The CLI signs with the login's project key.
func (a *App) SigningClient(ctx context.Context, apiClient *api.Client, project string) (*api.Client, error) {
	if a.HasExplicitCredential() || a.CredentialSource == "profile-token" || a.PlatformTokenFn == nil || project == "" {
		return apiClient, nil
	}
	keyed, _, err := a.workloadKeyClient(ctx, apiClient.BaseURL, project)
	if err != nil {
		return nil, err
	}
	keyed.Debug = apiClient.Debug
	return keyed, nil
}

// ToolboxClient returns the client for the sandbox's toolbox proxy given the
// API client that resolved the sandbox: a static credential is reused
// as-is; the CLI sends toolbox requests with the login's project KEY instead
// of its platform token. The key is minted with the
// JWT's expiry and cached for subsequent requests.
func (a *App) ToolboxClient(ctx context.Context, apiClient *api.Client, toolboxBase, project string) (*api.Client, error) {
	if a.HasExplicitCredential() || a.CredentialSource == "profile-token" || a.PlatformTokenFn == nil || project == "" {
		return apiClient.WithBaseURL(toolboxBase), nil
	}
	keyed, _, err := a.workloadKeyClient(ctx, toolboxBase, project)
	if err != nil {
		return nil, err
	}
	keyed.Debug = apiClient.Debug
	return keyed, nil
}

func (a *App) newClient(base, cred string) *api.Client {
	c := api.New(base, cred, Version)
	if a.Globals.Debug {
		c.Debug = func(format string, args ...any) { fmt.Fprintf(a.Stderr, "debug: "+format+"\n", args...) }
	}
	return c
}

// AnonClient returns an unauthenticated client (GET /config, OIDC discovery).
func (a *App) AnonClient() *api.Client {
	return api.New(a.APIURL(), "", Version)
}
