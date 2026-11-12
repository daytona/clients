// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

func TestConcurrentMintsConvergeAndRevokeTheLoser(t *testing.T) {
	// Given two invocations that both reach the mint before either returns.
	ctx, cancel := context.WithTimeout(t.Context(), 5*time.Second)
	defer cancel()
	var arrivals atomic.Int32
	both := make(chan struct{})
	f := &lifecycleAPI{onMint: func() {
		if arrivals.Add(1) == 2 {
			close(both)
		}
		select {
		case <-both:
		case <-ctx.Done():
		}
	}}
	srv := httptest.NewServer(http.HandlerFunc(f.serve))
	defer srv.Close()
	a := lifecycleApp(t, srv.URL)
	// Also prove revocation does not hold the store lock.
	f.onRevoke = func() {
		require.NoError(t, a.Store.Update(func(_ *config.Config, _ *config.Credentials) (bool, bool, error) { return false, false, nil }))
	}
	type result struct {
		key string
		err error
	}
	results := make(chan result, 2)
	// When both mints reconcile against the same profile cache.
	for range 2 {
		go func() {
			var key string
			var err error
			key, err = WorkloadCredential(ctx, a, lifecycleProject, "")
			results <- result{key, err}
		}()
	}
	first, second := <-results, <-results
	// Then both use the winner, exactly one loser is revoked, and one key persists.
	require.NoError(t, first.err)
	require.NoError(t, second.err)
	require.Equal(t, first.key, second.key)
	require.Len(t, f.requests, 2)
	require.Len(t, f.revoked, 1)
	cr, err := a.Store.LoadCredentials()
	require.NoError(t, err)
	keys := cr.Profiles[a.Profile.Name].WorkloadKeys
	require.Len(t, keys, 1)
	require.Equal(t, first.key, keys[0].Key)
	require.NotEqual(t, keys[0].KeyID, f.revoked[0])
}

func TestLogoutDuringMintRevokesResultWithoutRestoringSession(t *testing.T) {
	// Given logout completes while the mint HTTP request is in flight.
	f := &lifecycleAPI{}
	srv := httptest.NewServer(http.HandlerFunc(f.serve))
	defer srv.Close()
	a := lifecycleApp(t, srv.URL)
	f.onMint = func() { require.NoError(t, Logout(t.Context(), a)) }
	// When the mint returns after logout.
	var err error
	_, err = WorkloadCredential(t.Context(), a, lifecycleProject, "")
	// Then the losing key is revoked and no local session is resurrected.
	require.Equal(t, cerr.ExitAuth, cerr.ExitCode(err))
	require.Equal(t, []string{"33333333-3333-3333-3333-000000000001"}, f.revoked)
	cr, loadErr := a.Store.LoadCredentials()
	require.NoError(t, loadErr)
	require.NotContains(t, cr.Profiles, a.Profile.Name)
}

func TestReplacementDoesNotRevokeAnotherAPIOrigin(t *testing.T) {
	// Given a same-profile/project login key from another origin.
	f := &lifecycleAPI{}
	srv := httptest.NewServer(http.HandlerFunc(f.serve))
	defer srv.Close()
	a := lifecycleApp(t, srv.URL)
	require.NoError(t, a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		pc.WorkloadKeys = []config.WorkloadKey{{ProjectID: lifecycleProject, KeyID: "foreign", Key: "foreign", APIURL: "https://other.example", ExpiresAt: time.Now().Add(-time.Hour)}}
		cr.Profiles[a.Profile.Name] = pc
		return false, true, nil
	}))
	// When this API needs a fresh key.
	_, err := WorkloadCredential(t.Context(), a, lifecycleProject, "")
	// Then no revoke targets the foreign key, and its metadata is retained.
	require.NoError(t, err)
	require.Empty(t, f.revoked)
	cr, err := a.Store.LoadCredentials()
	require.NoError(t, err)
	require.Len(t, cr.Profiles[a.Profile.Name].WorkloadKeys, 2)
}

// A login is bound to its saved URL: with --api-url naming another API, the
// CLI neither mints a key there nor sends the login's token there.
func TestLoginIsNotUsedUnderAnotherAPIURL(t *testing.T) {
	saved, override := &lifecycleAPI{}, &lifecycleAPI{}
	savedSrv := httptest.NewServer(http.HandlerFunc(saved.serve))
	defer savedSrv.Close()
	overrideSrv := httptest.NewServer(http.HandlerFunc(override.serve))
	defer overrideSrv.Close()
	a := lifecycleApp(t, savedSrv.URL)

	a.Profile.APIURL = overrideSrv.URL // what --api-url resolves to
	_, err := WorkloadCredential(t.Context(), a, lifecycleProject, "")
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.Contains(t, err.Error(), "is not sent to "+overrideSrv.URL)
	require.Empty(t, override.requests)
	_, _, err = a.WorkloadClient(t.Context(), nil)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))

	a.Profile.APIURL = savedSrv.URL
	_, err = WorkloadCredential(t.Context(), a, lifecycleProject, "")
	require.NoError(t, err)
	require.Len(t, saved.requests, 1)
	require.Empty(t, override.requests)
}

func TestLoginMintCacheFailureRevokesTheUntrackedKey(t *testing.T) {
	// Given persistence fails only after the server has minted the key.
	f := &lifecycleAPI{}
	srv := httptest.NewServer(http.HandlerFunc(f.serve))
	defer srv.Close()
	a := lifecycleApp(t, srv.URL)
	f.onMint = func() {
		path := filepath.Join(a.Store.Dir, "credentials.yaml")
		require.NoError(t, os.Rename(path, path+".bak"))
		require.NoError(t, os.Mkdir(path, 0o700))
	}
	// When the login mint cannot persist its reconciliation result.
	key, err := WorkloadCredential(t.Context(), a, lifecycleProject, "")
	// Then no untracked credential is returned and the minted key is revoked.
	require.Error(t, err)
	require.Empty(t, key)
	require.Equal(t, []string{"33333333-3333-3333-3333-000000000001"}, f.revoked)
}
