// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"maps"
	"slices"
	"strings"
	"sync"
	"time"
)

// Sandbox is a concurrency-safe live handle. Obtain one from Client.Get, List,
// Search or FindByName. Metadata getters do not perform I/O.
type Sandbox struct {
	client                 *Client
	id                     string
	mu                     sync.RWMutex
	info                   SandboxInfo
	toolboxURL             string
	explicitToolboxURL     bool
	toolboxURLFromTemplate bool
	auth                   *sandboxCredential
	toolboxClient          *toolboxClient
}

type sandboxInit struct {
	Info SandboxInfo
	// ToolboxURL is a trusted API-provided proxy BASE, scoped once by newSandbox.
	ToolboxURL string
	Options    GetOptions
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
		toolboxURLFromTemplate: strings.Contains(init.Options.ToolboxURL, "{sandboxId}") ||
			strings.Contains(init.Options.ToolboxURL, "{sandbox_id}"),
	}
	token := init.Options.AccessToken
	if token == "" {
		token = init.Info.AccessToken
	}
	sandbox.auth = &sandboxCredential{
		sandbox: sandbox, cache: tokenCache{owner: c, now: time.Now},
		mintable: init.Options.AccessToken == "" || init.FromCreate,
	}
	sandbox.auth.cache.value = cachedToken{token: token}
	if init.FromCreate && !init.Info.AccessTokenExpiresAt.IsZero() {
		sandbox.auth.cache.value.expiresAt = init.Info.AccessTokenExpiresAt
		sandbox.auth.cache.value.refreshAt = init.Info.AccessTokenExpiresAt.Add(-time.Minute)
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
func (s *Sandbox) Info() SandboxInfo {
	s.mu.RLock()
	info := cloneSandboxInfo(s.info)
	s.mu.RUnlock()
	token := s.auth.cache.snapshot()
	info.AccessToken, info.AccessTokenExpiresAt = token.token, token.expiresAt
	return info
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
