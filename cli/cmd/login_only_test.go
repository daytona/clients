// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/spf13/cobra"
	"github.com/stretchr/testify/require"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
)

// testLoginBearer is a login's access token (shaped like a JWT) passed as a
// static bearer, which the login-only commands send. Tests that drive those
// commands with --api-key use it in place of an API key.
const testLoginBearer = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJl" // gitleaks:allow

// bearerFor is the static bearer a test passes for the command args name:
// testLoginBearer for a login-only command, else the API key "k".
func bearerFor(args []string) string {
	c, _, err := NewRoot(io.Discard, io.Discard).Find(args)
	if err == nil && isLoginOnly(c) {
		return testLoginBearer
	}
	return "k"
}

// loginOnlyLeaves returns every runnable login-only command with the fewest
// placeholder arguments its Args validator accepts.
func loginOnlyLeaves(t *testing.T) map[string][]string {
	t.Helper()
	out := map[string][]string{}
	var walk func(c *cobra.Command)
	walk = func(c *cobra.Command) {
		for _, sub := range c.Commands() {
			walk(sub)
		}
		if !isLoginOnly(c) || !c.Runnable() {
			return
		}
		for n := 0; n <= 3; n++ {
			args := make([]string, n)
			for i := range args {
				args[i] = "x"
			}
			if c.Args == nil || c.Args(c, args) == nil {
				out[commandPath(c)] = args
				return
			}
		}
		t.Fatalf("%s: no placeholder argument count passes its Args", commandPath(c))
	}
	walk(NewRoot(io.Discard, io.Discard))
	return out
}

func TestLoginOnlyCommandsAreTheExpectedFamilies(t *testing.T) {
	leaves := loginOnlyLeaves(t)
	families := map[string]bool{}
	for path := range leaves {
		family, _, _ := strings.Cut(path, " ")
		families[family] = true
	}
	for family := range loginOnlyFamilies {
		require.True(t, families[family], "family %s has no login-only command", family)
	}
	for _, path := range []string{"billing wallet", "billing usage", "org get", "org list", "org use", "key list", "org-key list", "member list", "invite accept", "project budget get", "project member list", "service-account list"} {
		require.Contains(t, leaves, path)
	}
	root := NewRoot(io.Discard, io.Discard)
	for _, args := range [][]string{{"org", "limits"}, {"audit", "list"}, {"region", "list"}, {"role", "list"}, {"whoami"}, {"sandbox", "list"}, {"billing"}, {"org"}} {
		c, _, err := root.Find(args)
		require.NoError(t, err)
		require.False(t, isLoginOnly(c), "%v must not be login-only", args)
	}
}

// Every login-only command refuses a static credential from any source before
// it selects an organization or sends a request, with --org or without, and
// says a login session is needed.
func TestLoginOnlyCommandsRefuseAStaticCredentialBeforeAnyRequest(t *testing.T) {
	leaves := loginOnlyLeaves(t)
	paths := make([]string, 0, len(leaves))
	for path := range leaves {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	sources := []struct {
		name   string
		flags  []string
		env    map[string]string
		saved  string
		naming string
	}{
		{name: "flag", flags: []string{"--api-key", "dtn_static"}, naming: "--api-key"},
		{name: "env", env: map[string]string{"DAYTONA_API_KEY": "dtn_static"}, naming: "DAYTONA_API_KEY"},
		{name: "profile-token", saved: "dtno_saved", naming: `profile "default"`},
	}
	for _, src := range sources {
		t.Run(src.name, func(t *testing.T) {
			f := newFakeAPI(t)
			d := cfgDir(t)
			if src.saved != "" {
				st, err := config.Open(d)
				require.NoError(t, err)
				require.NoError(t, st.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
					c.Upsert(config.Profile{Name: "default", APIURL: f.srv.URL, Credential: "token"})
					c.ActiveProfile = "default"
					cr.Profiles["default"] = config.ProfileCredentials{Token: src.saved}
					return true, true, nil
				}))
			}
			for _, path := range paths {
				for _, org := range [][]string{nil, {"--org", testOrgA}} {
					args := append(append(append(append([]string{}, src.flags...), "--api-url", f.srv.URL), org...), strings.Fields(path)...)
					args = append(args, leaves[path]...)
					_, errb, code := run(t, d, src.env, args...)
					require.Equal(t, cerr.ExitAuth, code, "%s %v: %s", path, org, errb)
					require.Contains(t, errb, "`daytona "+path+"` needs a `daytona login` session", path)
					require.Contains(t, errb, src.naming, path)
					require.Contains(t, errb, "daytona login", path)
					require.NotContains(t, errb, "credential rejected", path)
				}
			}
			require.Empty(t, f.requests, "a refused command must send nothing")
		})
	}
}

func TestLoginOnlyRefusalIsOneJSONErrorFromTheCLI(t *testing.T) {
	f := newFakeAPI(t)
	_, errb, code := runReported(t, cfgDir(t), map[string]string{"DAYTONA_API_KEY": "dtn_static"}, "--api-url", f.srv.URL, "billing", "wallet", "-o", "json")
	require.Equal(t, cerr.ExitAuth, code, errb)
	var body struct {
		Error map[string]any `json:"error"`
	}
	require.NoError(t, json.Unmarshal([]byte(strings.SplitN(errb, "\n", 2)[0]), &body), errb)
	require.Equal(t, "CLI", body.Error["source"])
	require.EqualValues(t, cerr.ExitAuth, body.Error["exit_code"])
	require.Contains(t, body.Error["hint"], "unset DAYTONA_API_KEY")
	require.Empty(t, f.requests)
}

// A login's access token passed as a static bearer (a JWT) is sent as usual.
func TestLoginOnlyCommandsSendAJWTShapedBearer(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET", "/orgs", 200, organizationsFixture())
	jwt := "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1In0.c2lnbmF0dXJl" // gitleaks:allow
	_, errb, code := run(t, cfgDir(t), nil, "--api-key", jwt, "--api-url", f.srv.URL, "org", "list", "-o", "json")
	require.Equal(t, 0, code, errb)
	require.Len(t, f.requests, 1)
	require.Equal(t, "Bearer "+jwt, f.requests[0].Auth)
}

func TestLooksLikeJWT(t *testing.T) {
	for s, want := range map[string]bool{
		"eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1In0.c2lnbmF0dXJl": true, // gitleaks:allow
		"a.b.c":      true,
		"dtn_static": false,
		"a.b":        false,
		"a..c":       false,
		"a.b.c.d":    false,
		"a.b c.d":    false,
		"a.b+/.c":    false,
		"":           false,
	} {
		require.Equal(t, want, looksLikeJWT(s), s)
	}
}

func TestLoginOnlyHelpSaysSo(t *testing.T) {
	for _, args := range [][]string{{"billing", "wallet", "--help"}, {"billing", "--help"}, {"org", "get", "--help"}, {"key", "list", "--help"}} {
		out, errb, code := run(t, cfgDir(t), nil, args...)
		require.Equal(t, 0, code, errb)
		require.Contains(t, out, "Needs a `daytona login` session", args)
	}
	for _, args := range [][]string{{"org", "limits", "--help"}, {"org", "--help"}, {"region", "list", "--help"}} {
		out, errb, code := run(t, cfgDir(t), nil, args...)
		require.Equal(t, 0, code, errb)
		require.NotContains(t, out, "Needs a `daytona login` session", args)
	}
}

// loginOnlyRoutes are the API routes the login-only command families call.
// Each must admit a user login session and nothing else, or the family list
// in login_only.go is wrong.
var loginOnlyRoutes = []struct {
	path   string
	prefix bool
	except []string
}{
	{path: "/me"},
	{path: "/orgs"},
	{path: "/orgs/{org_id}"},
	{path: "/orgs/{org_id}/billing/", prefix: true, except: []string{"/orgs/{org_id}/billing/region-multipliers"}},
	{path: "/orgs/{org_id}/invitations", prefix: true},
	{path: "/invitations/", prefix: true},
	{path: "/orgs/{org_id}/members", prefix: true},
	{path: "/orgs/{org_id}/api-keys", prefix: true, except: []string{"/orgs/{org_id}/api-keys/current"}},
	{path: "/orgs/{org_id}/service-accounts", prefix: true},
	{path: "/orgs/{org_id}/projects", prefix: true},
	{path: "/projects", prefix: true},
}

func TestLoginOnlyCommandsCallLoginOnlyRoutes(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "contract", "api-openapi.json"))
	require.NoError(t, err)
	var doc struct {
		Paths map[string]map[string]struct {
			Security []map[string][]string `json:"security"`
		} `json:"paths"`
	}
	require.NoError(t, json.Unmarshal(raw, &doc))
	schemes := func(path, method string) []string {
		seen := map[string]bool{}
		var names []string
		for _, req := range doc.Paths[path][method].Security {
			for name := range req {
				if !seen[name] {
					seen[name] = true
					names = append(names, name)
				}
			}
		}
		sort.Strings(names)
		return names
	}
	for _, route := range loginOnlyRoutes {
		matched := 0
		for path, ops := range doc.Paths {
			if path != route.path && (!route.prefix || !strings.HasPrefix(path, route.path)) {
				continue
			}
			excluded := false
			for _, e := range route.except {
				excluded = excluded || path == e
			}
			if excluded {
				continue
			}
			for method := range ops {
				switch method {
				case "get", "post", "put", "patch", "delete":
				default:
					continue
				}
				matched++
				require.Equal(t, []string{"user_jwt"}, schemes(path, method), "%s %s", strings.ToUpper(method), path)
			}
		}
		require.NotZero(t, matched, "no operation under %s", route.path)
	}
	// The exceptions really accept an API key: org limits takes a project
	// key, and the region multipliers (read by region list) an org key.
	require.Contains(t, schemes("/orgs/{org_id}/limits", "get"), "api_key")
	require.Contains(t, schemes("/orgs/{org_id}/billing/region-multipliers", "get"), "org_key")
}
