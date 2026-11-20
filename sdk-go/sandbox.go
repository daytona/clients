// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"maps"
	"slices"
	"sync"
	"time"
)

// Sandbox is a concurrency-safe live handle. Obtain one from Client.Get, List,
// Search or FindByName. Metadata getters do not perform I/O.
type Sandbox struct {
	client             *Client
	id                 string
	mu                 sync.RWMutex
	info               SandboxInfo
	toolboxURL         string
	explicitToolboxURL bool
	auth               *sandboxCredential
	toolboxClient      *toolboxClient
}

type sandboxInit struct {
	Info SandboxInfo
	// ToolboxURL is a trusted API-provided proxy BASE, scoped once by newSandbox.
	ToolboxURL string
	Options    GetSandboxOptions
	// AccessToken and AccessTokenExpiresAt carry an API-issued sandbox token
	// (a create or fork response); they are kept out of SandboxInfo so that
	// printing metadata never prints a credential.
	AccessToken          string
	AccessTokenExpiresAt time.Time
	// FromCreate permits refreshing the initial, API-issued token.
	FromCreate bool
}

// newSandbox is also the construction seam for create/fork implementations.
func (c *Client) newSandbox(init sandboxInit) (*Sandbox, error) {
	endpoint, err := scopedToolboxURL(init.ToolboxURL, init.Info.ID, init.Options.ToolboxURL)
	if err != nil {
		return nil, err
	}
	sandbox := &Sandbox{
		client: c, id: init.Info.ID, info: cloneSandboxInfo(init.Info),
		toolboxURL: endpoint, explicitToolboxURL: init.Options.ToolboxURL != "",
	}
	token := init.Options.AccessToken
	if token == "" {
		token = init.AccessToken
	}
	sandbox.auth = &sandboxCredential{
		sandbox: sandbox, cache: tokenCache{owner: c, now: time.Now},
		mintable: init.Options.AccessToken == "" || init.FromCreate,
	}
	sandbox.auth.cache.value = cachedToken{token: token}
	if init.FromCreate && !init.AccessTokenExpiresAt.IsZero() {
		sandbox.auth.cache.value.expiresAt = init.AccessTokenExpiresAt
		sandbox.auth.cache.value.refreshAt = init.AccessTokenExpiresAt.Add(-time.Minute)
	}
	sandbox.toolboxClient = &toolboxClient{sandbox: sandbox}
	return sandbox, nil
}

// ID returns the sandbox's stable ID.
func (s *Sandbox) ID() string { return s.id }

// Name returns the last observed name, or empty before it was reported.
func (s *Sandbox) Name() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.info.Name
}

// State returns the last observed state, or SandboxStateUnknown before a read.
func (s *Sandbox) State() SandboxState {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.info.State == "" {
		return SandboxStateUnknown
	}
	return s.info.State
}

// Info returns an independent copy, including maps, slices and optional values.
// It never contains a credential; see AccessToken.
func (s *Sandbox) Info() SandboxInfo {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return cloneSandboxInfo(s.info)
}

// AccessToken returns the sandbox access token the handle currently holds and
// when it expires (zero when unknown). The token is a credential for this
// sandbox's toolbox: never log or print it. It is empty until the API issued
// one or GetSandboxOptions supplied one; the SDK mints and refreshes it as
// needed when the handle calls the toolbox.
func (s *Sandbox) AccessToken() (token string, expiresAt time.Time) {
	current := s.auth.cache.snapshot()
	return current.token, current.expiresAt
}

// Refresh reloads metadata. It returns ErrNotFound if the sandbox no longer
// exists, or ErrClientClosed after the owning client closes.
func (s *Sandbox) Refresh(ctx context.Context) error {
	observation, err := s.client.getSandbox(ctx, s.id)
	if err != nil {
		return err
	}
	endpoint, err := scopedToolboxURL(observation.toolboxURL, s.id, "")
	if err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.info = observation.info
	s.info.ID = s.id
	if !s.explicitToolboxURL && endpoint != "" {
		s.toolboxURL = endpoint
	}
	return nil
}

func (s *Sandbox) toolbox() *toolboxClient { return s.toolboxClient }

func clonePointer[T any](value *T) *T {
	if value == nil {
		return nil
	}
	cloned := *value
	return &cloned
}

func cloneSandboxInfo(info SandboxInfo) SandboxInfo {
	out := info
	out.Labels = maps.Clone(info.Labels)
	out.PortMap = slices.Clone(info.PortMap)
	out.Image = clonePointer(info.Image)
	out.Snapshot = clonePointer(info.Snapshot)
	out.Persistent = clonePointer(info.Persistent)
	out.Spot = clonePointer(info.Spot)
	out.Resources.CPUMax = clonePointer(info.Resources.CPUMax)
	out.Resources.MemoryMaxGiB = clonePointer(info.Resources.MemoryMaxGiB)
	out.Resources.DiskMaxGiB = clonePointer(info.Resources.DiskMaxGiB)
	out.LifecyclePolicy = clonePointer(info.LifecyclePolicy)
	if out.LifecyclePolicy != nil {
		out.LifecyclePolicy.Timeout = clonePointer(info.LifecyclePolicy.Timeout)
		out.LifecyclePolicy.IdleExpiry = clonePointer(info.LifecyclePolicy.IdleExpiry)
	}
	return out
}

// String returns a credential-free description.
func (s *Sandbox) String() string { return "daytona.Sandbox(" + s.id + ")" }

// GoString returns a credential-free description.
func (s *Sandbox) GoString() string { return s.String() }
