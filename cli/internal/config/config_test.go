// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package config

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestDir(t *testing.T) {
	d, _ := Dir(func(k string) string {
		if k == "DAYTONA_CONFIG_DIR" {
			return "/x"
		}
		return ""
	})
	if d != "/x" {
		t.Fatal(d)
	}
	d, _ = Dir(func(k string) string {
		if k == "XDG_CONFIG_HOME" {
			return "/xdg"
		}
		return ""
	})
	if d != filepath.Join("/xdg", "daytona") {
		t.Fatal(d)
	}
}

func TestRoundTripAndModes(t *testing.T) {
	dir := t.TempDir()
	s, err := Open(filepath.Join(dir, "cfg"))
	if err != nil {
		t.Fatal(err)
	}
	err = s.Update(func(c *Config, cr *Credentials) (bool, bool, error) {
		c.Upsert(Profile{Name: "default", APIURL: "https://api", Credential: "oidc", OrgID: "o1", ProjectByOrg: map[string]string{"o1": "p1"}})
		c.ActiveProfile = "default"
		cr.Profiles["default"] = ProfileCredentials{OIDC: &Tokens{AccessToken: "a", ExpiresAt: time.Now().Add(time.Hour)}}
		return true, true, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	c, _ := s.LoadConfig()
	p, ok := c.Profile("default")
	if !ok || p.ProjectID() != "p1" {
		t.Fatalf("%+v", c)
	}
	cr, _ := s.LoadCredentials()
	if cr.Profiles["default"].OIDC.AccessToken != "a" {
		t.Fatal()
	}
	if runtime.GOOS != "windows" {
		for _, f := range []string{"config.yaml", "credentials.yaml"} {
			st, err := os.Stat(filepath.Join(s.Dir, f))
			if err != nil {
				t.Fatal(err)
			}
			if st.Mode().Perm() != 0o600 {
				t.Fatalf("%s mode %o", f, st.Mode().Perm())
			}
		}
		st, _ := os.Stat(s.Dir)
		if st.Mode().Perm() != 0o700 {
			t.Fatalf("dir mode %o", st.Mode().Perm())
		}
	}
	if !c.Remove("default") || c.ActiveProfile != "" {
		t.Fatal("remove")
	}
}

func TestConcurrentUpdates(t *testing.T) {
	s, _ := Open(filepath.Join(t.TempDir(), "c"))
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_ = s.Update(func(c *Config, _ *Credentials) (bool, bool, error) {
				c.Upsert(Profile{Name: string(rune('a' + i)), APIURL: "u"})
				return true, false, nil
			})
		}(i)
	}
	wg.Wait()
	c, _ := s.LoadConfig()
	if len(c.Profiles) != 20 {
		t.Fatalf("lost updates: %d profiles", len(c.Profiles))
	}
	if entries, _ := os.ReadDir(s.Dir); len(entries) > 3 {
		t.Fatalf("temp files leaked: %d entries", len(entries))
	}
}

func TestSettingsDefault(t *testing.T) {
	if !(Settings{}).ConfirmEnabled() {
		t.Fatal()
	}
	f := false
	if (Settings{Confirm: &f}).ConfirmEnabled() {
		t.Fatal()
	}
}

func TestOpenTightensExistingDir(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX modes")
	}
	dir := filepath.Join(t.TempDir(), "loose")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(dir); err != nil {
		t.Fatal(err)
	}
	st, _ := os.Stat(dir)
	if st.Mode().Perm() != 0o700 {
		t.Fatalf("existing dir left at %o", st.Mode().Perm())
	}
}

// A pre-existing or later-relaxed credentials.yaml is tightened to 0600 on
// every Open, not only when the CLI writes it.
func TestOpenTightensExistingCredentialsFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX modes")
	}
	dir := t.TempDir()
	creds := filepath.Join(dir, "credentials.yaml")
	if err := os.WriteFile(creds, []byte("profiles: {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(dir); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(creds)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o600 {
		t.Fatalf("credentials.yaml mode %o, want 0600", st.Mode().Perm())
	}
}

// A symlinked credentials.yaml is refused rather than chmod'ed/written through.
func TestOpenRefusesSymlinkedCredentialsFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX symlinks")
	}
	dir := t.TempDir()
	target := filepath.Join(t.TempDir(), "victim")
	if err := os.WriteFile(target, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, filepath.Join(dir, "credentials.yaml")); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(dir); err == nil || !strings.Contains(err.Error(), "symlink") {
		t.Fatalf("expected a symlink refusal, got %v", err)
	}
	st, err := os.Stat(target)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o644 {
		t.Fatalf("the symlink target must be untouched, mode %o", st.Mode().Perm())
	}
}

// Keys an earlier release exchanged from a static credential (Source set)
// are never used again; the next write of credentials.yaml drops them.
func TestSaveCredentials_dropsLegacyStaticExchangeKeys(t *testing.T) {
	st, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	shared := []WorkloadKey{{KeyID: "login"}, {KeyID: "legacy", Key: "secret-fixture", Source: "fingerprint"}}
	cr := &Credentials{Profiles: map[string]ProfileCredentials{"a": {WorkloadKeys: shared}, "b": {WorkloadKeys: shared}}}
	if err := st.SaveCredentials(cr); err != nil {
		t.Fatal(err)
	}
	got, err := st.LoadCredentials()
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"a", "b"} {
		keys := got.Profiles[name].WorkloadKeys
		if len(keys) != 1 || keys[0].KeyID != "login" {
			t.Fatalf("%s: %+v", name, keys)
		}
	}
	raw, err := os.ReadFile(filepath.Join(st.Dir, "credentials.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "secret-fixture") {
		t.Fatal("the legacy key is still on disk")
	}
}

func TestSaveCredentials_leavesTheCallersValueUnchanged(t *testing.T) {
	st, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	cr := &Credentials{Profiles: map[string]ProfileCredentials{"a": {WorkloadKeys: []WorkloadKey{{KeyID: "legacy", Source: "x"}}}}}
	if err := st.SaveCredentials(cr); err != nil {
		t.Fatal(err)
	}
	if len(cr.Profiles["a"].WorkloadKeys) != 1 {
		t.Fatal("SaveCredentials changed the value it was given")
	}
}
