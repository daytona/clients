// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"sort"
	"strings"
	"time"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/config"
)

// cachedWorkloadKeyAny is the zero-network warm path. Expired IDs are retained
// until the network path can attempt cleanup, rather than silently pruned here.
func cachedWorkloadKeyAny(a *app.App, projectID string) (string, bool, error) {
	// Entries without an API URL use the saved profile URL as their origin.
	c, err := a.Store.LoadConfig()
	if err != nil {
		return "", false, err
	}
	p, ok := c.Profile(a.Profile.Name)
	profileOrigin := ok && strings.TrimRight(p.APIURL, "/") == a.APIURL()
	return lookupWorkloadKey(a, func(k config.WorkloadKey) bool {
		return k.ProjectID == projectID && k.Source == "" && ((k.APIURL == "" && profileOrigin) || k.APIURL == a.APIURL())
	})
}

func lookupWorkloadKey(a *app.App, want func(config.WorkloadKey) bool) (string, bool, error) {
	var key string
	err := a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		for _, k := range cr.Profiles[a.Profile.Name].WorkloadKeys {
			if want(k) && k.ExpiresAt.After(time.Now().Add(refreshSkew)) {
				key = k.Key
			}
		}
		return false, false, nil
	})
	return key, key != "", err
}

// Fingerprint hashes a permission set order-independently.
func Fingerprint(perms []string) string {
	cp := append([]string(nil), perms...)
	sort.Strings(cp)
	b, _ := json.Marshal(cp)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:8])
}

func shortHost(h string) string {
	h = strings.ToLower(strings.SplitN(h, ".", 2)[0])
	if len(h) > 16 {
		h = h[:16]
	}
	if h == "" {
		h = "host"
	}
	return h
}

func randSuffix() string {
	s, err := randomURLSafe(6)
	if err != nil {
		return "000000"
	}
	return strings.ToLower(strings.NewReplacer("-", "x", "_", "y").Replace(s))
}

// ForgetWorkloadKeys follows org leave, whose server side already revokes keys.
func ForgetWorkloadKeys(a *app.App, projectIDs map[string]bool) error {
	return a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		kept := pc.WorkloadKeys[:0]
		for _, k := range pc.WorkloadKeys {
			if !projectIDs[k.ProjectID] {
				kept = append(kept, k)
			}
		}
		pc.WorkloadKeys = kept
		tokens := pc.PlatformTokens[:0]
		for _, t := range pc.PlatformTokens {
			if !projectIDs[t.ProjectID] {
				tokens = append(tokens, t)
			}
		}
		pc.PlatformTokens = tokens
		cr.Profiles[a.Profile.Name] = pc
		return false, true, nil
	})
}
