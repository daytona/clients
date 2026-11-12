// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package app carries the per-invocation runtime every command receives:
// resolved global flags, presentation mode, config store, and lazily built
// API clients. It is the one place credential selection happens:
// an explicitly supplied credential is the only
// credential for the invocation; otherwise the active profile's.
package app

import (
	"context"
	"io"
	"net/url"
	"os"
	"strings"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/ui"
)

// Version is stamped at build time (hack/build.sh -ldflags).
var Version = "0.0.0-dev"

// DefaultAPIURL is the production Daytona API, used when neither --api-url,
// DAYTONA_V2_API_URL nor the active profile names one. It matches the SDKs'
// DEFAULT_V2_API_URL.
const DefaultAPIURL = "https://api.daytona.com"

// ForceTTY overrides the isatty probes (nil = probe). A test seam: command
// tests run without a terminal but must exercise the interactive-only paths
// (the browser login flow). Never set in production code.
var ForceTTY *bool

// Globals are the persistent root flags plus the per-command flags every
// family shares (--file, --no-wait, --timeout are registered only on the
// commands that use them; unset elsewhere).
type Globals struct {
	APIURL string
	APIKey string // explicit static credential (--api-key / DAYTONA_API_KEY)
	// APIKeySet reports that --api-key was given on the command line, even
	// as an empty value: an explicitly empty credential is a usage error,
	// never a fall-through to DAYTONA_API_KEY or the saved login.
	APIKeySet bool
	Profile   string
	// ProfileMayBeNew lets a profile named by the --profile flag that does
	// not exist yet through (`login --profile NAME` creates it). Otherwise an
	// explicit unknown profile is "not found", except on the commands
	// ProfileOptional marks.
	ProfileMayBeNew bool
	// CreatesProfile marks `login`: a dangling active profile does not stop
	// it, so signing in again is always possible.
	CreatesProfile bool
	// ProfileOptional marks a command that does not act on the selected
	// profile (help, completion, docs, `profile *`, `config *`): an unknown
	// --profile / DAYTONA_PROFILE does not stop it.
	ProfileOptional bool
	Org             string
	Project         string
	Region          string
	// Output is --output as given; "" selects the default: table when stdout
	// is a terminal, JSON otherwise.
	Output  string
	Yes     bool
	NoWait  bool
	Timeout string
	File    string
	Quiet   bool
	Debug   bool
}

// App is the resolved runtime.
type App struct {
	Globals Globals
	Mode    ui.Mode
	Format  output.Format
	Store   *config.Store
	Config  *config.Config
	Profile *config.Profile // active (may be a synthetic one when --api-key/--api-url only; APIURL is always resolved)
	Stdout  io.Writer
	Stderr  io.Writer
	Getenv  func(string) string

	// Ctx is the invocation's context (cancelled by Ctrl-C / SIGTERM); a
	// terminal prompt stops reading when it ends. Nil means Background.
	Ctx context.Context

	// CredentialSource says where the bearer came from: "flag", "env",
	// "profile-token", "profile-oidc", "" (none). whoami shows it; nothing
	// else branches on it.
	CredentialSource string

	// credentialFn resolves the account credential lazily (OIDC refresh
	// happens here) — set by the auth package via SetCredentialResolver.
	credentialFn func(ctx context.Context) (string, error)

	// OrgResolver turns an organization NAME given via --org / DAYTONA_ORG
	// into its id (installed by the cmd layer; needs the account API client).
	OrgResolver     func(ctx context.Context, ref string) (string, error)
	ProjectResolver func(ctx context.Context, orgID, ref string) (string, error)
	// orgFromOverride marks that Profile.OrgID came from --org/DAYTONA_ORG
	// (and so may be a name) rather than from the saved profile (always an id).
	orgFromOverride bool
	orgIDResolved   bool
	// projectFromOverride marks a project named by --project / DAYTONA_PROJECT.
	projectFromOverride bool

	// savedAPIURL is the API the selected profile's saved credential was
	// issued for: the URL its login saved, else DefaultAPIURL.
	savedAPIURL string
	// savedCredential marks that the credential is the profile's saved one
	// (installed by SetCredentialResolver), which is bound to savedAPIURL.
	savedCredential bool

	// StaticKeyProjectFn describes the static credential's project when the
	// server knows it as a project API key (ok=false otherwise). Installed by
	// the cmd layer; called at most once per invocation (see staticKeyProject).
	StaticKeyProjectFn func(ctx context.Context) (StaticKeyProject, bool, error)
	staticKeyProject   *staticKeyProjectResult

	// PlatformTokenFn returns a valid project-scoped platform access token
	// for `project`, exchanged from the login's OIDC JWT (RFC 8693 at
	// POST /auth/token) — the API bearer on a deployment that is a
	// platform-token issuer. ok=false (no
	// error) means this deployment is not an issuer: the caller falls back
	// to a minted project key. `rejected` is a cached token the server just
	// refused (or ""), dropped before a fresh exchange. Installed by auth.
	PlatformTokenFn func(ctx context.Context, project, rejected string) (token string, ok bool, err error)

	// CachedWorkloadKeyFn returns an unexpired CLI-owned project key the
	// login minted earlier for `project` ("" if none) — zero network: the
	// warm path of every sandbox command. Installed by the auth package.
	CachedWorkloadKeyFn func(project string) string

	// WorkloadCredentialFn resolves the bearer for sandbox/toolbox routes of
	// one project when nothing usable is cached:
	// for an OIDC login it reads the ceiling from GET /orgs and mints a
	// project key. `rejected` is a cached key the server just refused (or
	// ""): it is dropped first, so a key minted under a since-widened
	// ceiling is replaced by one under the current ceiling. Installed by the
	// auth package; nil until then.
	WorkloadCredentialFn func(ctx context.Context, projectID, rejected string) (string, error)
}

// StaticKeyProject is the project a project API key acts in.
type StaticKeyProject struct {
	ID, Name, OrgID, OrgName string
}

type staticKeyProjectResult struct {
	project StaticKeyProject
	ok      bool
	err     error
}

// Load builds the App from globals + environment.
func Load(g Globals, stdout, stderr io.Writer, getenv func(string) string) (*App, error) {
	if getenv == nil {
		getenv = os.Getenv
	}
	a := &App{Globals: g, Stdout: stdout, Stderr: stderr, Getenv: getenv}

	stdoutTTY := WriterIsTerminal(stdout)
	f, err := ResolveFormat(g.Output, stdoutTTY)
	if err != nil {
		return nil, err
	}
	a.Format = f
	if g.APIKeySet && strings.TrimSpace(g.APIKey) == "" {
		return nil, cerr.Usage("--api-key is empty").
			WithHint("pass a credential, or omit --api-key to use DAYTONA_API_KEY or the saved login")
	}

	dir, err := config.Dir(getenv)
	if err != nil {
		return nil, err
	}
	st, err := config.Open(dir)
	if err != nil {
		return nil, err
	}
	a.Store = st
	cfg, err := st.LoadConfig()
	if err != nil {
		return nil, err
	}
	a.Config = cfg

	a.Mode = ui.Resolve(ui.Options{OutputFormat: string(f), YesFlag: g.Yes, ConfirmOff: !cfg.Settings.ConfirmEnabled(), Getenv: getenv, StdoutTTY: &stdoutTTY, StdinTTY: ForceTTY})

	// Profile selection: --profile > DAYTONA_PROFILE > active > "default".
	// A profile named explicitly must exist, except for `login --profile`,
	// which creates it, and for commands that do not use the selected
	// profile; the active or implicit `default` profile of a fresh install is
	// synthesized.
	fromFlag := strings.TrimSpace(g.Profile) != ""
	explicit := firstNonEmpty(g.Profile, getenv("DAYTONA_PROFILE"))
	name := firstNonEmpty(explicit, cfg.ActiveProfile, "default")
	if p, ok := cfg.Profile(name); ok {
		cp := *p
		a.Profile = &cp
	} else if (explicit != "" || (cfg.ActiveProfile != "" && !g.CreatesProfile)) && !g.ProfileOptional && !(g.ProfileMayBeNew && fromFlag) {
		e := cerr.New(cerr.ExitNotFound, "no profile %q", name)
		switch {
		case fromFlag:
			return nil, e.WithHint("`daytona profile list` shows the saved profiles; `daytona login --profile %s` creates one", name)
		case explicit != "":
			return nil, e.WithHint("DAYTONA_PROFILE names a profile that does not exist: unset it, or create the profile with `daytona login --profile %s`; `daytona profile list` shows the saved ones", name)
		default:
			return nil, e.WithHint("the active profile does not exist: `daytona profile list` shows the saved profiles; select one with `daytona profile use NAME`, or create it with `daytona login --profile %s`", name)
		}
	} else {
		a.Profile = &config.Profile{Name: name}
	}

	// API URL: --api-url > DAYTONA_V2_API_URL > the profile's saved URL >
	// DefaultAPIURL. The variable matches the SDKs; it and the flag exist for
	// test environments. DAYTONA_API_URL belongs to the 0.x CLI and SDKs and
	// is not read.
	a.savedAPIURL = resolveAPIURL(a.Profile.APIURL)
	a.Profile.APIURL = resolveAPIURL(g.APIURL, getenv("DAYTONA_V2_API_URL"), a.Profile.APIURL)
	if err := checkAPIURL(a.Profile.APIURL); err != nil && !g.ProfileOptional {
		return nil, err
	}

	// Explicit overrides. The credential is sent exactly as given.
	if g.APIKeySet {
		a.CredentialSource = "flag"
		a.credentialFn = staticCred(g.APIKey)
	} else if k := getenv("DAYTONA_API_KEY"); strings.TrimSpace(k) != "" {
		a.CredentialSource = "env"
		a.credentialFn = staticCred(k)
	}
	if o := firstNonEmpty(g.Org, getenv("DAYTONA_ORG")); o != "" {
		a.Profile.OrgID = o
		a.orgFromOverride = true
	}
	if p := firstNonEmpty(g.Project, getenv("DAYTONA_PROJECT")); p != "" {
		if a.Profile.ProjectByOrg == nil {
			a.Profile.ProjectByOrg = map[string]string{}
		}
		a.Profile.ProjectByOrg[a.Profile.OrgID] = p
		a.projectFromOverride = true
	}
	return a, nil
}

// sameAPIURL compares two resolved API URLs with the scheme and host
// case-insensitive, as URLs are.
func sameAPIURL(x, y string) bool {
	ux, errX := url.Parse(x)
	uy, errY := url.Parse(y)
	if errX != nil || errY != nil {
		return x == y
	}
	return strings.EqualFold(ux.Scheme, uy.Scheme) && strings.EqualFold(ux.Host, uy.Host) && ux.EscapedPath() == uy.EscapedPath()
}

// checkAPIURL refuses an API URL that is not an absolute http(s) URL with a
// host, as a usage error, before any request.
func checkAPIURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" || u.User != nil || u.ForceQuery || u.RawQuery != "" || strings.Contains(raw, "#") {
		return cerr.Usage("invalid API URL %q: want an absolute http:// or https:// URL with a host", raw).
			WithHint("check --api-url, DAYTONA_V2_API_URL, or the profile's saved URL")
	}
	return nil
}

// APIURL returns the API base URL of this invocation (see resolveAPIURL).
func (a *App) APIURL() string {
	return resolveAPIURL(a.Profile.APIURL)
}

// resolveAPIURL returns the first candidate that is not blank, with
// surrounding whitespace and trailing slashes dropped, or DefaultAPIURL. As
// in the SDKs, a blank, whitespace-only or slash-only value counts as unset.
func resolveAPIURL(candidates ...string) string {
	for _, c := range candidates {
		if u := strings.TrimRight(strings.TrimSpace(c), "/"); u != "" {
			return u
		}
	}
	return DefaultAPIURL
}

func firstNonEmpty(s ...string) string {
	for _, v := range s {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}
