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
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/ui"
	"golang.org/x/term"
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
	} else if explicit != "" && !g.ProfileOptional && !(g.ProfileMayBeNew && fromFlag) {
		e := cerr.New(cerr.ExitNotFound, "no profile %q", name)
		if fromFlag {
			return nil, e.WithHint("`daytona profile list` shows the saved profiles; `daytona login --profile %s` creates one", name)
		}
		return nil, e.WithHint("DAYTONA_PROFILE names a profile that does not exist: unset it, or create the profile with `daytona login --profile %s`; `daytona profile list` shows the saved ones", name)
	} else {
		a.Profile = &config.Profile{Name: name}
	}

	// API URL: --api-url > DAYTONA_V2_API_URL > the profile's saved URL >
	// DefaultAPIURL. The variable matches the SDKs; it and the flag exist for
	// test environments. DAYTONA_API_URL belongs to the 0.x CLI and SDKs and
	// is not read.
	a.Profile.APIURL = resolveAPIURL(g.APIURL, getenv("DAYTONA_V2_API_URL"), a.Profile.APIURL)

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
	}
	return a, nil
}

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
}

// ReplaceCredentialResolver installs a resolver unconditionally. Used only by
// `login`, whose whole purpose is to change the credential for the rest of
// the invocation (the context-setup calls that follow must use the tokens
// just saved, not a previously stored static credential).
func (a *App) ReplaceCredentialResolver(source string, fn func(ctx context.Context) (string, error)) {
	a.CredentialSource = source
	a.credentialFn = fn
}

// OrgFromOverride reports whether Profile.OrgID came from --org / DAYTONA_ORG
// rather than from the saved profile.
func (a *App) OrgFromOverride() bool { return a.orgFromOverride }

// HasExplicitCredential reports whether --api-key / DAYTONA_API_KEY was given.
func (a *App) HasExplicitCredential() bool {
	return a.CredentialSource == "flag" || a.CredentialSource == "env"
}

// Credential returns the bearer for this invocation.
func (a *App) Credential(ctx context.Context) (string, error) {
	if a.credentialFn == nil {
		return "", cerr.New(cerr.ExitAuth, "not logged in").
			WithHint("run `daytona login`, or pass --api-key / set DAYTONA_API_KEY")
	}
	return a.credentialFn(ctx)
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

// Printer returns the stdout printer.
func (a *App) Printer() output.Printer {
	return output.Printer{W: a.Stdout, Format: a.Format, Color: a.Mode.Color}
}

// Info prints a diagnostic line to stderr unless --quiet.
func (a *App) Info(format string, args ...any) {
	if a.Globals.Quiet {
		return
	}
	// Arguments are often server-controlled (names, messages) — strip
	// terminal control sequences before they reach a TTY.
	fmt.Fprintln(a.Stderr, output.Sanitize(fmt.Sprintf(format, args...)))
}

// PreflightConfirm is the network-free half of Confirm: in
// non-interactive mode a destructive verb without --yes must fail with exit 2
// BEFORE any request is made, so an agent gets the promised refusal rather
// than a network error or a resolved-then-refused round trip. Call it first
// in RunE with the raw argument; Confirm (with the resolved, human-readable
// name) still runs later for the interactive prompt.
func (a *App) PreflightConfirm(verb, target string) error {
	if a.Mode.AssumeYes || a.Mode.Interactive {
		return nil
	}
	return cerr.RefuseWithoutYes(verb, target)
}

// Confirm prompts on a TTY and requires --yes otherwise.
func (a *App) Confirm(verb, target string) error {
	if a.Mode.AssumeYes {
		return nil
	}
	if !a.Mode.Interactive {
		return cerr.RefuseWithoutYes(verb, target)
	}
	ok, err := ui.Confirm(fmt.Sprintf("%s %s?", capitalize(verb), target))
	if err != nil {
		return err
	}
	if !ok {
		return cerr.New(cerr.ExitInterrupted, "aborted")
	}
	return nil
}

// RequireOrg returns the selected org id or a usage error / picker.
func (a *App) RequireOrg(ctx context.Context, pick func(ctx context.Context) (string, error)) (string, error) {
	if a.Profile.OrgID != "" {
		if err := a.resolveSelectedOrg(ctx); err != nil {
			return "", err
		}
		return a.Profile.OrgID, nil
	}
	if a.Mode.Interactive && pick != nil {
		return pick(ctx)
	}
	return "", cerr.MissingArg("an organization", "--org (or `daytona org use`)")
}

func looksLikeUUID(s string) bool {
	if len(s) != 36 {
		return false
	}
	for i, ch := range s {
		switch i {
		case 8, 13, 18, 23:
			if ch != '-' {
				return false
			}
		default:
			if !((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') || (ch >= 'A' && ch <= 'F')) {
				return false
			}
		}
	}
	return true
}

// RequireProject returns the selected project id or a usage error / picker.
func (a *App) RequireProject(ctx context.Context, pick func(ctx context.Context) (string, error)) (string, error) {
	if project, err := a.resolveSelectedProject(ctx); err != nil {
		return "", err
	} else if project != "" {
		return project, nil
	}
	if a.Mode.Interactive && pick != nil {
		return pick(ctx)
	}
	return "", cerr.MissingArg("a project", "--project (or `daytona project use`)")
}

func (a *App) resolveSelectedOrg(ctx context.Context) error {
	if a.Profile == nil || a.Profile.OrgID == "" || a.orgIDResolved || !a.orgFromOverride || a.OrgResolver == nil || looksLikeUUID(a.Profile.OrgID) {
		return nil
	}
	raw := a.Profile.OrgID
	id, err := a.OrgResolver(ctx, raw)
	if err != nil {
		return err
	}
	if id != raw && a.Profile.ProjectByOrg != nil {
		if project, ok := a.Profile.ProjectByOrg[raw]; ok {
			delete(a.Profile.ProjectByOrg, raw)
			a.Profile.ProjectByOrg[id] = project
		}
	}
	a.Profile.OrgID = id
	a.orgFromOverride = false
	a.orgIDResolved = true
	return nil
}

func (a *App) resolveSelectedProject(ctx context.Context) (string, error) {
	if a.Profile == nil {
		return "", nil
	}
	if err := a.resolveSelectedOrg(ctx); err != nil {
		return "", err
	}
	project := strings.TrimSpace(a.Profile.ProjectID())
	if project == "" {
		return project, nil
	}
	if looksLikeUUID(project) {
		// One spelling for the caches keyed by it (workload keys, platform
		// tokens): `ABC…` and `abc…` are the same project.
		return strings.ToLower(project), nil
	}
	if a.ProjectResolver == nil {
		return project, nil
	}
	// A name — and the literal `default`, which project settings routes accept as
	// an alias but the workload-key cache, the static key mint and the
	// platform-token exchange all need as a UUID — is resolved within the
	// selected org. Without an org there is nothing to resolve against: a
	// project-bound key with `--project` set to anything works regardless
	// of the value, so the override passes through verbatim rather than
	// building `/orgs//projects`.
	if a.Profile.OrgID == "" {
		return project, nil
	}
	resolved, err := a.ProjectResolver(ctx, a.Profile.OrgID, project)
	if err != nil {
		return "", err
	}
	if a.Profile.ProjectByOrg == nil {
		a.Profile.ProjectByOrg = map[string]string{}
	}
	a.Profile.ProjectByOrg[a.Profile.OrgID] = resolved
	return resolved, nil
}

// ResolveFormat parses --output; an empty value selects the default: table
// when stdout is a terminal, JSON otherwise.
func ResolveFormat(flag string, stdoutTTY bool) (output.Format, error) {
	if strings.TrimSpace(flag) == "" {
		if stdoutTTY {
			return output.FormatTable, nil
		}
		return output.FormatJSON, nil
	}
	f, err := output.ParseFormat(flag)
	if err != nil {
		return "", cerr.Usage("%v", err)
	}
	return f, nil
}

// WriterIsTerminal reports whether w is a terminal (ForceTTY overrides the
// probe in tests). Anything that is not an *os.File is not a terminal.
func WriterIsTerminal(w io.Writer) bool {
	if ForceTTY != nil {
		return *ForceTTY
	}
	f, ok := w.(*os.File)
	return ok && term.IsTerminal(int(f.Fd()))
}

func firstNonEmpty(s ...string) string {
	for _, v := range s {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

func capitalize(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}
