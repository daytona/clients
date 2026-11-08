// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/daytona/clients/cli/internal/mgmt"
	"github.com/google/uuid"
)

const workloadKeyLifetime = 24 * time.Hour

type keyMint struct {
	app               *app.App
	client            *api.Client
	project, rejected string
	permissions       []string
}

// mint checks/snapshots, mints outside the lock, then reconciles. Only keys
// from this profile, project and API can be superseded. Revoke is
// best-effort outside the lock; failed cleanup is bounded by server expiry.
func (m keyMint) mint(ctx context.Context) (string, error) {
	a := m.app
	ceiling := Fingerprint(m.permissions)
	var session, key, profileBase string
	err := a.Store.Update(func(c *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		if pc.OIDC == nil {
			return false, false, cerr.New(cerr.ExitAuth, "not logged in").WithHint("run `daytona login`")
		}
		if pc.OIDC.AccessToken != m.client.Credential {
			return false, false, cerr.New(cerr.ExitAuth, "session changed while minting a project key").WithHint("run the command again")
		}
		if p, ok := c.Profile(a.Profile.Name); ok {
			profileBase = strings.TrimRight(p.APIURL, "/")
		}
		for _, k := range pc.WorkloadKeys {
			if m.matches(k, profileBase) && k.Key != m.rejected && k.Ceiling == ceiling && k.ExpiresAt.After(time.Now().Add(refreshSkew)) {
				key = k.Key
			}
		}
		if key != "" {
			return false, false, nil
		}
		changed := pc.KeySession == ""
		if changed {
			pc.KeySession = uuid.NewString()
			cr.Profiles[a.Profile.Name] = pc
		}
		session = pc.KeySession
		return false, changed, nil
	})
	if err != nil || key != "" {
		return key, err
	}
	host, _ := os.Hostname()
	exp := time.Now().Add(workloadKeyLifetime)
	syncToV0 := false
	resp, err := mgmt.CreateKey(ctx, m.client, m.project, gen.CreateProjectApiKeyRequest{
		Name:               mgmt.Ref(fmt.Sprintf("cli-%s-%s-%s", a.Profile.Name, shortHost(host), randSuffix())),
		ProjectPermissions: m.permissions, ExpiresAt: &exp, SyncToV0: &syncToV0,
	})
	if err != nil {
		return "", err
	}
	if resp.V.ExpiresAt != nil {
		exp = *resp.V.ExpiresAt
	}
	minted := config.WorkloadKey{ProjectID: m.project, KeyID: resp.V.Id.String(), Key: resp.V.Token, Name: resp.V.Name, ExpiresAt: exp, Ceiling: ceiling, APIURL: m.client.BaseURL}
	var superseded []config.WorkloadKey
	err = a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc, ok := cr.Profiles[a.Profile.Name]
		if !ok || pc.KeySession != session || pc.OIDC == nil {
			return false, false, cerr.New(cerr.ExitAuth, "session changed while minting a project key").WithHint("run the command again")
		}
		kept := make([]config.WorkloadKey, 0, len(pc.WorkloadKeys)+1)
		for _, k := range pc.WorkloadKeys {
			if !m.matches(k, profileBase) {
				kept = append(kept, k)
				continue
			}
			if key == "" && k.Key != m.rejected && k.Ceiling == ceiling && k.ExpiresAt.After(time.Now().Add(refreshSkew)) {
				key = k.Key
				kept = append(kept, k) // concurrent winner
			} else {
				superseded = append(superseded, k)
			}
		}
		if key == "" {
			key = minted.Key
			kept = append(kept, minted)
		}
		pc.WorkloadKeys = kept
		cr.Profiles[a.Profile.Name] = pc
		return false, true, nil
	})
	if err != nil {
		revokeKeys(ctx, a, m.client, []config.WorkloadKey{minted})
		return "", err
	}
	if key != minted.Key {
		superseded = append(superseded, minted)
	}
	revokeKeys(ctx, a, m.client, superseded)
	return key, nil
}

func (m keyMint) matches(k config.WorkloadKey, profileBase string) bool {
	// Entries with a Source were exchanged from a static credential by an
	// earlier CLI release; nothing uses them, and the next save drops them.
	if k.ProjectID != m.project || k.Source != "" {
		return false
	}
	if k.APIURL != "" {
		return k.APIURL == m.client.BaseURL
	}
	// Login entries belong to the saved URL, never the invocation's override.
	return profileBase == m.client.BaseURL
}
