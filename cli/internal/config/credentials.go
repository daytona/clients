// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package config

import "time"

// Tokens are one OIDC login's tokens.
type Tokens struct {
	AccessToken  string    `yaml:"access_token"`
	RefreshToken string    `yaml:"refresh_token,omitempty"`
	IDToken      string    `yaml:"id_token,omitempty"`
	ExpiresAt    time.Time `yaml:"expires_at"`
	// The minting native client belongs to these tokens, not the profile override.
	ClientID string `yaml:"client_id,omitempty"`
}

// WorkloadKey is one cached auto-minted project key.
type WorkloadKey struct {
	ProjectID string    `yaml:"project_id"`
	KeyID     string    `yaml:"key_id"`
	Key       string    `yaml:"key"`
	Name      string    `yaml:"name"`
	ExpiresAt time.Time `yaml:"expires_at"`
	Ceiling   string    `yaml:"ceiling"`
	// Empty for login-minted keys. Earlier releases also cached keys they
	// exchanged from a static credential, marked with a fingerprint here;
	// nothing uses them any more and SaveCredentials leaves them out.
	Source string `yaml:"source,omitempty"`
	// Cleanup goes to this issuing API, never an invocation's URL override.
	// Login keys without it belong to the saved profile URL.
	APIURL string `yaml:"api_url,omitempty"`
}

type ProfileCredentials struct {
	// KeySession fences in-flight mints against logout/credential replacement.
	KeySession     string          `yaml:"key_session,omitempty"`
	Token          string          `yaml:"token,omitempty"`
	OIDC           *Tokens         `yaml:"oidc,omitempty"`
	WorkloadKeys   []WorkloadKey   `yaml:"workload_keys,omitempty"`
	PlatformTokens []PlatformToken `yaml:"platform_tokens,omitempty"`
}

// PlatformToken is one cached RFC 8693 exchange result (300 s).
type PlatformToken struct {
	ProjectID string    `yaml:"project_id"`
	Token     string    `yaml:"token"`
	ExpiresAt time.Time `yaml:"expires_at"`
	// The API that minted the token; it is only ever sent back there.
	// Entries without it (written by older releases) are not reused.
	APIURL string `yaml:"api_url,omitempty"`
}

type Credentials struct {
	Profiles map[string]ProfileCredentials `yaml:"profiles"`
}

// withoutLegacyKeys returns a copy of c without the keys earlier releases
// exchanged from a static credential (a non-empty Source); c is unchanged.
// Those keys expire server-side within 24 hours.
func (c *Credentials) withoutLegacyKeys() *Credentials {
	out := &Credentials{Profiles: make(map[string]ProfileCredentials, len(c.Profiles))}
	for name, pc := range c.Profiles {
		var kept []WorkloadKey
		for _, k := range pc.WorkloadKeys {
			if k.Source == "" {
				kept = append(kept, k)
			}
		}
		pc.WorkloadKeys = kept
		out.Profiles[name] = pc
	}
	return out
}
