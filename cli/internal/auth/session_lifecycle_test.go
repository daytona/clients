// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

const lifecycleProject = "22222222-2222-2222-2222-222222222222"
const lifecycleOrg = "11111111-1111-1111-1111-111111111111"

type lifecycleAPI struct {
	mu       sync.Mutex
	requests []gen.CreateProjectApiKeyRequest
	revoked  []string
	onMint   func()
	onRevoke func()
}

func (f *lifecycleAPI) serve(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	switch {
	case r.URL.Path == "/orgs":
		if err := json.NewEncoder(w).Encode(map[string]any{"organizations": []map[string]any{{"id": lifecycleOrg, "name": "org", "membership": map[string]any{"grantable_project_permissions": []string{"read:sandboxes"}}}}}); err != nil {
			http.Error(w, "fixture encoding failed", http.StatusInternalServerError)
		}
	case r.Method == http.MethodPost && r.URL.Path == "/projects/"+lifecycleProject+"/api-keys":
		var req gen.CreateProjectApiKeyRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(400)
			return
		}
		f.mu.Lock()
		f.requests = append(f.requests, req)
		n := len(f.requests)
		f.mu.Unlock()
		if f.onMint != nil {
			f.onMint()
		}
		if err := json.NewEncoder(w).Encode(map[string]any{"id": fmt.Sprintf("33333333-3333-3333-3333-%012d", n), "name": "cli", "token": fmt.Sprintf("minted-%d", n), "expires_at": req.ExpiresAt}); err != nil {
			http.Error(w, "fixture encoding failed", http.StatusInternalServerError)
		}
	case r.Method == http.MethodDelete:
		f.mu.Lock()
		f.revoked = append(f.revoked, strings.TrimPrefix(r.URL.Path, "/projects/"+lifecycleProject+"/api-keys/"))
		f.mu.Unlock()
		if f.onRevoke != nil {
			f.onRevoke()
		}
		w.WriteHeader(204)
	default:
		w.WriteHeader(404)
	}
}

func lifecycleApp(t *testing.T, base string) *app.App {
	t.Helper()
	a := newSessionTestApp(t, base)
	a.Profile.OrgID = lifecycleOrg
	require.NoError(t, a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		pc.OIDC = &config.Tokens{AccessToken: "jwt", ExpiresAt: time.Now().Add(5 * time.Minute)}
		cr.Profiles[a.Profile.Name] = pc
		return false, true, nil
	}))
	Install(a)
	return a
}

func TestAutomaticKeysAreV2OnlyAndLive24Hours(t *testing.T) {
	// Given a short access-token lifetime.
	f := &lifecycleAPI{}
	srv := httptest.NewServer(http.HandlerFunc(f.serve))
	defer srv.Close()
	a := lifecycleApp(t, srv.URL)
	before := time.Now()
	// When the CLI automatically mints a key.
	_, err := WorkloadCredential(t.Context(), a, lifecycleProject, "")
	// Then neither bridge is opted into, and the request expires 24h after mint.
	require.NoError(t, err)
	f.mu.Lock()
	requests := append([]gen.CreateProjectApiKeyRequest(nil), f.requests...)
	f.mu.Unlock()
	require.Len(t, requests, 1)
	req := requests[0]
	require.NotNil(t, req.SyncToV0)
	require.False(t, *req.SyncToV0)
	require.WithinRange(t, *req.ExpiresAt, before.Add(24*time.Hour), time.Now().Add(24*time.Hour))
}

func TestReplacementRevokesOnlySupersededLoginKeys(t *testing.T) {
	for _, reason := range []string{"expired", "ceiling", "rejected"} {
		t.Run(reason, func(t *testing.T) {
			// Given an old login key and a key an earlier release exchanged
			// from a static credential, in the same profile.
			f := &lifecycleAPI{}
			srv := httptest.NewServer(http.HandlerFunc(f.serve))
			defer srv.Close()
			a := lifecycleApp(t, srv.URL)
			old := config.WorkloadKey{ProjectID: lifecycleProject, KeyID: "old-id", Key: "old", Ceiling: Fingerprint([]string{"read:sandboxes"}), ExpiresAt: time.Now().Add(time.Hour)}
			rejected := ""
			switch reason {
			case "expired":
				old.ExpiresAt = time.Now().Add(-time.Minute)
			case "ceiling":
				old.Ceiling = "narrow"
			case "rejected":
				rejected = old.Key
			}
			require.NoError(t, a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
				pc := cr.Profiles[a.Profile.Name]
				other := old
				other.KeyID = "other-id"
				other.Source = "other-source"
				pc.WorkloadKeys = []config.WorkloadKey{old, other}
				cr.Profiles[a.Profile.Name] = pc
				return false, true, nil
			}))
			// When expiry, a changed ceiling, or rejection causes replacement.
			_, err := WorkloadCredential(t.Context(), a, lifecycleProject, rejected)
			// Then cleanup revokes only the superseded login key.
			require.NoError(t, err)
			require.Equal(t, []string{"old-id"}, f.revoked)
		})
	}
}

func TestWarmLookupPreservesExpiredIDsForCleanup(t *testing.T) {
	// Given an expired key that still needs a server revoke opportunity.
	a := lifecycleApp(t, "http://127.0.0.1:1")
	require.NoError(t, a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		pc.WorkloadKeys = []config.WorkloadKey{{KeyID: "old-id", Key: "old", ProjectID: lifecycleProject, ExpiresAt: time.Now().Add(-time.Hour)}}
		cr.Profiles[a.Profile.Name] = pc
		return false, true, nil
	}))
	// When the zero-network warm lookup misses.
	_, ok, err := cachedWorkloadKeyAny(a, lifecycleProject)
	// Then the unusable key's ID remains available for replacement/logout cleanup.
	require.NoError(t, err)
	require.False(t, ok)
	cr, err := a.Store.LoadCredentials()
	require.NoError(t, err)
	require.Len(t, cr.Profiles[a.Profile.Name].WorkloadKeys, 1)
}
