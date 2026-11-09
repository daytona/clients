// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"fmt"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/auth"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/google/uuid"
)

func loginOIDC(ctx context.Context, a *app.App, device, browser bool, clientID string) error {
	anon := a.AnonClient()
	sc, err := auth.FetchServerConfig(ctx, anon)
	if err != nil {
		return err
	}
	if clientID == "" {
		clientID = a.Profile.OIDCClientID
	}
	p, err := auth.ProviderFor(ctx, anon.HTTP, sc, clientID, a.Getenv)
	if err != nil {
		return err
	}
	var ts *auth.TokenSet
	if !device && !browser && a.Mode.Interactive && isSSHSession(a.Getenv) {
		a.Info("SSH session detected; using device login (a browser on your machine cannot reach this host's loopback redirect; pass --browser if you forward the port with ssh -L).")
		device = true
	}
	if device || !a.Mode.Interactive {
		if !device {
			a.Info("No terminal detected; using device login.")
		}
		dc, err := auth.StartDevice(ctx, anon.HTTP, p)
		if err != nil {
			return err
		}
		// Flatten IdP-provided text before rendering to the terminal.
		fmt.Fprintln(a.Stderr, auth.FormatDevicePrompt(&auth.DeviceCode{
			DeviceCode: dc.DeviceCode, UserCode: output.SanitizeLine(dc.UserCode),
			VerificationURI: output.SanitizeLine(dc.VerificationURI), VerificationURIComplete: output.SanitizeLine(dc.VerificationURIComplete),
			ExpiresIn: dc.ExpiresIn, Interval: dc.Interval,
		}))
		fmt.Fprintln(a.Stderr, "Waiting for approval…")
		ts, err = auth.PollDevice(ctx, anon.HTTP, p, dc, nil)
		if err != nil {
			return err
		}
	} else {
		pk, err := auth.NewPKCE()
		if err != nil {
			return err
		}
		lb, err := auth.ListenLoopback(pk.State)
		if err != nil {
			return err
		}
		lb.Notice = func(line string) { fmt.Fprintln(a.Stderr, output.SanitizeLine(line)) }
		u := auth.AuthorizeURL(p, pk, lb.RedirectURI())
		// Always print the URL; opening a browser is only a convenience.
		if err := auth.OpenBrowser(u); err != nil {
			fmt.Fprintf(a.Stderr, "Open this URL to sign in:\n%s\n", output.SanitizeLine(u))
		} else {
			fmt.Fprintf(a.Stderr, "Opened your browser to sign in. If it did not open, visit:\n%s\n", output.SanitizeLine(u))
		}
		res, err := lb.Wait(ctx, 5*time.Minute)
		if err != nil {
			return err
		}
		if res.Err != "" {
			return cerr.New(cerr.ExitAuth, "login failed: %s", res.Err)
		}
		ts, err = auth.ExchangeCode(ctx, anon.HTTP, p, pk, res.Code, lb.RedirectURI())
		if err != nil {
			return err
		}
	}
	now, name, base := time.Now(), a.Profile.Name, anon.BaseURL
	loginSession := uuid.NewString()
	var cleanup auth.LoginKeyCleanup
	err = a.Store.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		// Only an explicit client-id becomes a profile override.
		prof := config.Profile{Name: name, APIURL: base, Credential: "oidc", OIDCClientID: clientID}
		previous := config.Profile{Name: name}
		if cur, ok := c.Profile(name); ok {
			prof.OrgID, prof.ProjectByOrg, prof.Identity = cur.OrgID, cur.ProjectByOrg, cur.Identity
			previous = *cur
		}
		c.Upsert(prof)
		if c.ActiveProfile == "" {
			c.ActiveProfile = name
		}
		pc := cr.Profiles[name]
		cleanup = auth.SnapshotLoginKeys(previous, pc)
		// KeySession fences in-flight writers, not cache hits. Revoke every
		// old login key atomically with the new tokens, before /me can block.
		pc.WorkloadKeys, pc.PlatformTokens = nil, nil
		pc.KeySession = loginSession
		pc.Token = ""
		pc.OIDC = &config.Tokens{AccessToken: ts.AccessToken, RefreshToken: ts.RefreshToken, IDToken: ts.IDToken, ExpiresAt: ts.ExpiresAt(now), ClientID: p.ClientID}
		cr.Profiles[name] = pc
		return true, true, nil
	})
	cleanup.Revoke(ctx, a)
	if err != nil {
		return err
	}
	// Context setup uses the tokens just saved, never an explicit static override.
	a.ReplaceCredentialResolver("profile-oidc", func(ctx context.Context) (string, error) { return auth.AccessToken(ctx, a) })
	a.Profile.APIURL = base
	mc, err := a.Client(ctx)
	if err != nil {
		return err
	}
	me, err := mgmt.GetMe(ctx, mc)
	if err != nil {
		return err
	}
	orgs, err := mgmt.ListOrgs(ctx, mc)
	if err != nil {
		return err
	}
	cfg, err := a.Store.LoadConfig()
	if err != nil {
		return err
	}
	prof, ok := cfg.Profile(name)
	if !ok {
		return cerr.New(cerr.ExitAuth, "profile %q disappeared during login", name).WithHint("run `daytona login` again")
	}
	meID := me.V.Id.String()
	keep := prof.Identity == meID && prof.OrgID != ""
	if keep {
		if _, err := mgmt.ResolveOrg(orgs, prof.OrgID); err != nil {
			keep = false
		}
	}
	var org *gen.OrganizationListItem
	var fallbackMessage string
	if keep {
		org, _ = mgmt.ResolveOrg(orgs, prof.OrgID)
	} else {
		for i := range orgs {
			if orgs[i].Personal {
				org = &orgs[i]
				break
			}
		}
		if org == nil && len(orgs) > 0 {
			org = &orgs[0]
		}
	}
	projectID := ""
	if org != nil {
		ps, err := mgmt.ListProjects(ctx, mc, org.Id.String())
		if err != nil {
			return err
		}
		if keep {
			projectID = prof.ProjectByOrg[org.Id.String()]
			if projectID != "" {
				if _, err := mgmt.ResolveProject(ps, projectID); err != nil {
					remembered := projectID
					projectID = ""
					if org.Membership.DefaultProjectId != uuid.Nil {
						projectID = org.Membership.DefaultProjectId.String()
						fallbackMessage = fmt.Sprintf("Remembered project %s is no longer reachable in %s; fell back to default project %s.", remembered, org.Name, projectID)
					}
				}
			}
		}
		if projectID == "" && org.Membership.DefaultProjectId != uuid.Nil {
			projectID = org.Membership.DefaultProjectId.String()
		}
	}
	err = a.Store.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		if pc := cr.Profiles[name]; pc.OIDC == nil || pc.KeySession != loginSession {
			return false, false, cerr.New(cerr.ExitAuth, "session changed during login").WithHint("run `daytona login` again")
		}
		p, ok := c.Profile(name)
		if !ok {
			return false, false, cerr.New(cerr.ExitAuth, "profile %q disappeared during login", name).WithHint("run `daytona login` again")
		}
		if p.Identity != meID {
			p.ProjectByOrg = map[string]string{}
		}
		p.Identity = meID
		if org != nil {
			p.OrgID = org.Id.String()
			if projectID != "" {
				if p.ProjectByOrg == nil {
					p.ProjectByOrg = map[string]string{}
				}
				p.ProjectByOrg[org.Id.String()] = projectID
			}
		} else {
			p.OrgID = ""
		}
		return true, true, nil
	})
	if err != nil {
		return err
	}
	who := me.V.Email
	if who == "" {
		who = meID
	}
	if fallbackMessage != "" {
		a.Info("%s", fallbackMessage)
	}
	if org != nil {
		a.Info("Signed in as %s. Organization: %s%s", who, org.Name, projectLabel(projectID))
	} else {
		a.Info("Signed in as %s (no organizations yet).", who)
	}
	return nil
}

func projectLabel(id string) string {
	if id == "" {
		return ""
	}
	return " · project " + id
}
