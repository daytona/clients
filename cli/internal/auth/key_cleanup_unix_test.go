// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build !windows

package auth

import (
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
	"golang.org/x/sys/unix"
)

func TestLogout_revokesSnapshot_whenConfigWriteFailsAfterCredentialsPersist(t *testing.T) {
	// Given a login with a cached key and config readable once, then unwritable.
	f := &lifecycleAPI{}
	server := httptest.NewServer(http.HandlerFunc(f.serve))
	defer server.Close()
	a := lifecycleApp(t, server.URL)
	require.NoError(t, a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		pc.WorkloadKeys = []config.WorkloadKey{{ProjectID: lifecycleProject, KeyID: "old-id", APIURL: server.URL, ExpiresAt: time.Now().Add(time.Hour)}}
		cr.Profiles[a.Profile.Name] = pc
		return false, true, nil
	}))
	path := filepath.Join(a.Store.Dir, "config.yaml")
	data, err := os.ReadFile(path)
	require.NoError(t, err)
	require.NoError(t, os.Remove(path))
	require.NoError(t, unix.Mkfifo(path, 0o600))
	writer := make(chan error, 1)
	go func() {
		// The FIFO rendezvous guarantees LoadConfig opened it. Replace its
		// name before EOF, so SaveConfig's later rename fails, not the read.
		pipe, openErr := os.OpenFile(path, os.O_WRONLY, 0)
		if openErr != nil {
			writer <- openErr
			return
		}
		_, writeErr := pipe.Write(data)
		removeErr := os.Remove(path)
		mkdirErr := os.Mkdir(path, 0o700)
		writer <- errors.Join(writeErr, removeErr, mkdirErr, pipe.Close())
	}()

	// When logout persists credentials but cannot persist the config update.
	err = Logout(t.Context(), a)

	// Then the error is preserved, but the otherwise-lost key is still revoked.
	require.NoError(t, <-writer)
	require.Error(t, err)
	require.Contains(t, fmt.Sprint(err), "credentials were saved")
	f.mu.Lock()
	revoked := append([]string(nil), f.revoked...)
	f.mu.Unlock()
	require.Equal(t, []string{"old-id"}, revoked)
	cr, loadErr := a.Store.LoadCredentials()
	require.NoError(t, loadErr)
	require.NotContains(t, cr.Profiles, a.Profile.Name)
}

func TestDeleteProfile_keepsKeys_whenCredentialsWriteFails(t *testing.T) {
	// Given a login with a cached key and credentials readable once, then unwritable.
	f := &lifecycleAPI{}
	server := httptest.NewServer(http.HandlerFunc(f.serve))
	defer server.Close()
	a := lifecycleApp(t, server.URL)
	require.NoError(t, a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		pc.WorkloadKeys = []config.WorkloadKey{{ProjectID: lifecycleProject, KeyID: "old-id", APIURL: server.URL, ExpiresAt: time.Now().Add(time.Hour)}}
		cr.Profiles[a.Profile.Name] = pc
		return false, true, nil
	}))
	path := filepath.Join(a.Store.Dir, "credentials.yaml")
	data, err := os.ReadFile(path)
	require.NoError(t, err)
	require.NoError(t, os.Remove(path))
	require.NoError(t, unix.Mkfifo(path, 0o600))
	writer := make(chan error, 1)
	go func() {
		// The FIFO rendezvous guarantees LoadCredentials opened it. Replace
		// its name before EOF, so SaveCredentials' later rename fails.
		pipe, openErr := os.OpenFile(path, os.O_WRONLY, 0)
		if openErr != nil {
			writer <- openErr
			return
		}
		_, writeErr := pipe.Write(data)
		removeErr := os.Remove(path)
		mkdirErr := os.Mkdir(path, 0o700)
		writer <- errors.Join(writeErr, removeErr, mkdirErr, pipe.Close())
	}()

	// When the delete cannot persist the credential removal.
	err = DeleteProfile(t.Context(), a, a.Profile.Name, func() error { return errors.New("not found") })

	// Then the error is returned and the keys the profile still holds stay live.
	require.NoError(t, <-writer)
	require.Error(t, err)
	f.mu.Lock()
	revoked := append([]string(nil), f.revoked...)
	f.mu.Unlock()
	require.Empty(t, revoked)
}

func TestDeleteProfile_revokesSnapshot_whenConfigWriteFailsAfterCredentialsPersist(t *testing.T) {
	// Given a login with a cached key and config readable once, then unwritable.
	f := &lifecycleAPI{}
	server := httptest.NewServer(http.HandlerFunc(f.serve))
	defer server.Close()
	a := lifecycleApp(t, server.URL)
	require.NoError(t, a.Store.Update(func(_ *config.Config, cr *config.Credentials) (bool, bool, error) {
		pc := cr.Profiles[a.Profile.Name]
		pc.WorkloadKeys = []config.WorkloadKey{{ProjectID: lifecycleProject, KeyID: "old-id", APIURL: server.URL, ExpiresAt: time.Now().Add(time.Hour)}}
		cr.Profiles[a.Profile.Name] = pc
		return false, true, nil
	}))
	path := filepath.Join(a.Store.Dir, "config.yaml")
	data, err := os.ReadFile(path)
	require.NoError(t, err)
	require.NoError(t, os.Remove(path))
	require.NoError(t, unix.Mkfifo(path, 0o600))
	writer := make(chan error, 1)
	go func() {
		pipe, openErr := os.OpenFile(path, os.O_WRONLY, 0)
		if openErr != nil {
			writer <- openErr
			return
		}
		_, writeErr := pipe.Write(data)
		removeErr := os.Remove(path)
		mkdirErr := os.Mkdir(path, 0o700)
		writer <- errors.Join(writeErr, removeErr, mkdirErr, pipe.Close())
	}()

	// When the credentials are removed but the config update fails.
	err = DeleteProfile(t.Context(), a, a.Profile.Name, func() error { return errors.New("not found") })

	// Then the error is preserved, but the otherwise-lost key is still revoked.
	require.NoError(t, <-writer)
	require.Error(t, err)
	f.mu.Lock()
	revoked := append([]string(nil), f.revoked...)
	f.mu.Unlock()
	require.Equal(t, []string{"old-id"}, revoked)
}
