// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package config owns the CLI's persistent state:
// profiles (API URL + credential + org/project context), the per-org
// remembered project, and the workload-key cache. Everything lives under a
// private directory (0700) in owner-only files (0600), written atomically
// under a file lock so concurrent invocations never interleave.
//
// Layout:
//
//	$DAYTONA_CONFIG_DIR | $XDG_CONFIG_HOME/daytona | ~/.config/daytona
//	  config.yaml        profiles, active profile, settings
//	  credentials.yaml   tokens + cached workload keys, per profile
//	  .lock              flock target
package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"

	"github.com/gofrs/flock"
	"gopkg.in/yaml.v3"
)

// Settings are user preferences.
type Settings struct {
	// Confirm controls destructive-verb prompts (default true).
	Confirm *bool `yaml:"confirm,omitempty"`
}

// ConfirmEnabled applies the default.
func (s Settings) ConfirmEnabled() bool { return s.Confirm == nil || *s.Confirm }

// Profile is one named connection.
type Profile struct {
	Name   string `yaml:"name"`
	APIURL string `yaml:"api_url"`
	// Credential names how this profile authenticates: "oidc" (browser/device
	// login; tokens in credentials.yaml) or "token" (a static bearer passed
	// as-is — project key, org key, or any future server-admitted credential;
	// the CLI never inspects it).
	Credential string `yaml:"credential,omitempty"`
	// OIDCClientID overrides the client id published by GET /config.
	OIDCClientID string `yaml:"oidc_client_id,omitempty"`
	// OrgID is the selected organization (OIDC profiles).
	OrgID string `yaml:"org_id,omitempty"`
	// ProjectByOrg remembers the last project used in each org.
	ProjectByOrg map[string]string `yaml:"project_by_org,omitempty"`
	// Identity is the /me id the context was saved for; a different identity
	// on re-login resets the context.
	Identity string `yaml:"identity,omitempty"`
}

// ProjectID returns the remembered project for the selected org.
func (p Profile) ProjectID() string {
	if p.ProjectByOrg == nil {
		return ""
	}
	return p.ProjectByOrg[p.OrgID]
}

// Config is config.yaml.
type Config struct {
	ActiveProfile string    `yaml:"active_profile"`
	Profiles      []Profile `yaml:"profiles"`
	Settings      Settings  `yaml:"settings"`
}

// Store reads and writes the two files under one directory.
type Store struct {
	Dir string
}

// Dir resolves the config directory.
func Dir(getenv func(string) string) (string, error) {
	if d := getenv("DAYTONA_CONFIG_DIR"); d != "" {
		return d, nil
	}
	if x := getenv("XDG_CONFIG_HOME"); x != "" {
		return filepath.Join(x, "daytona"), nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("resolve home directory: %w", err)
	}
	return filepath.Join(home, ".config", "daytona"), nil
}

// Open returns a Store for dir, creating it 0700 — and tightening an
// EXISTING directory to 0700 too: refresh tokens and workload keys are
// written here, and a pre-existing group/world-readable directory (a
// hand-made `~/.config/daytona`, a permissive XDG_CONFIG_HOME) would expose
// them to other local users. Windows has no POSIX mode bits; its default
// per-user ACL on %APPDATA%-style paths is the equivalent.
func Open(dir string) (*Store, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("create config dir %s: %w", dir, err)
	}
	if runtime.GOOS != "windows" {
		st, err := os.Stat(dir)
		if err != nil {
			return nil, fmt.Errorf("stat config dir %s: %w", dir, err)
		}
		if st.Mode().Perm() != 0o700 {
			if err := os.Chmod(dir, 0o700); err != nil {
				return nil, fmt.Errorf("tighten config dir %s to 0700: %w", dir, err)
			}
		}
		// The same for a pre-existing credentials.yaml: the CLI writes it
		// 0600, but a hand-copied or later-relaxed file would otherwise expose
		// refresh tokens and keys to other local users on every read.
		// Lstat, not Stat: a symlink planted here must not make the CLI chmod
		// (or later overwrite) whatever it points at — refuse it outright.
		creds := filepath.Join(dir, "credentials.yaml")
		if st, err := os.Lstat(creds); err == nil {
			if st.Mode()&os.ModeSymlink != 0 {
				return nil, fmt.Errorf("%s is a symlink; the CLI refuses to use a credentials file it does not own — remove it", creds)
			}
			if st.Mode().Perm() != 0o600 {
				if err := os.Chmod(creds, 0o600); err != nil {
					return nil, fmt.Errorf("tighten %s to 0600: %w", creds, err)
				}
			}
		}
	}
	return &Store{Dir: dir}, nil
}

func (s *Store) configPath() string { return filepath.Join(s.Dir, "config.yaml") }
func (s *Store) credsPath() string  { return filepath.Join(s.Dir, "credentials.yaml") }
func (s *Store) lockPath() string   { return filepath.Join(s.Dir, ".lock") }

// Lock takes the store's exclusive lock; the returned func releases it.
func (s *Store) Lock() (func(), error) {
	fl := flock.New(s.lockPath())
	if err := fl.Lock(); err != nil {
		return nil, fmt.Errorf("lock %s: %w", s.lockPath(), err)
	}
	return func() { _ = fl.Unlock() }, nil
}

// LoadConfig reads config.yaml (empty Config if absent).
func (s *Store) LoadConfig() (*Config, error) {
	var c Config
	if err := readYAML(s.configPath(), &c); err != nil {
		return nil, err
	}
	return &c, nil
}

// SaveConfig writes config.yaml atomically.
func (s *Store) SaveConfig(c *Config) error { return writeYAML(s.configPath(), c) }

// LoadCredentials reads credentials.yaml (empty if absent).
func (s *Store) LoadCredentials() (*Credentials, error) {
	var c Credentials
	if err := readYAML(s.credsPath(), &c); err != nil {
		return nil, err
	}
	if c.Profiles == nil {
		c.Profiles = map[string]ProfileCredentials{}
	}
	return &c, nil
}

// SaveCredentials writes credentials.yaml atomically, without the keys
// earlier releases exchanged from static credentials.
func (s *Store) SaveCredentials(c *Credentials) error {
	return writeYAML(s.credsPath(), c.withoutLegacyKeys())
}

// Update runs fn under the lock with fresh copies of both files and persists
// whatever fn returns (nil = leave that file untouched).
func (s *Store) Update(fn func(c *Config, cr *Credentials) (saveConfig, saveCreds bool, err error)) error {
	unlock, err := s.Lock()
	if err != nil {
		return err
	}
	defer unlock()
	c, err := s.LoadConfig()
	if err != nil {
		return err
	}
	cr, err := s.LoadCredentials()
	if err != nil {
		return err
	}
	sc, scr, err := fn(c, cr)
	if err != nil {
		return err
	}
	// Two files, each written atomically, but not as one transaction. Order
	// them so a crash between the two never yields a profile that CLAIMS a
	// credential it does not hold: credentials land first, then the config
	// that points at them. The reverse failure (credentials saved, config
	// not) leaves an orphaned entry the next login overwrites.
	if scr {
		if err := s.SaveCredentials(cr); err != nil {
			return err
		}
	}
	if sc {
		if err := s.SaveConfig(c); err != nil {
			return fmt.Errorf("%w (credentials were saved; re-run the command to complete the profile update)", err)
		}
	}
	return nil
}

// Profile finds a profile by name.
func (c *Config) Profile(name string) (*Profile, bool) {
	for i := range c.Profiles {
		if c.Profiles[i].Name == name {
			return &c.Profiles[i], true
		}
	}
	return nil, false
}

// Upsert adds or replaces a profile.
func (c *Config) Upsert(p Profile) {
	if cur, ok := c.Profile(p.Name); ok {
		*cur = p
		return
	}
	c.Profiles = append(c.Profiles, p)
}

// Remove deletes a profile; false if absent.
func (c *Config) Remove(name string) bool {
	for i := range c.Profiles {
		if c.Profiles[i].Name == name {
			c.Profiles = append(c.Profiles[:i], c.Profiles[i+1:]...)
			if c.ActiveProfile == name {
				c.ActiveProfile = ""
			}
			return true
		}
	}
	return false
}

func readYAML(path string, v any) error {
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("read %s: %w", path, err)
	}
	if err := yaml.Unmarshal(b, v); err != nil {
		return fmt.Errorf("parse %s: %w", path, err)
	}
	return nil
}

// writeYAML writes v to path via a same-dir temp file + rename (atomic on
// POSIX and on NTFS), mode 0600.
func writeYAML(path string, v any) error {
	b, err := yaml.Marshal(v)
	if err != nil {
		return err
	}
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return fmt.Errorf("create temp in %s: %w", dir, err)
	}
	tmpName := tmp.Name()
	cleanup := func() { _ = os.Remove(tmpName) }
	if err := tmp.Chmod(0o600); err != nil {
		_ = tmp.Close()
		cleanup()
		return err
	}
	if _, err := tmp.Write(b); err != nil {
		_ = tmp.Close()
		cleanup()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		cleanup()
		return err
	}
	if err := tmp.Close(); err != nil {
		cleanup()
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		cleanup()
		return fmt.Errorf("replace %s: %w", path, err)
	}
	return nil
}
