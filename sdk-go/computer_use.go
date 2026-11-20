// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
)

// ComputerUse controls the desktop addon. Obtain it from Sandbox.ComputerUse.
// Operations can return ErrAddonUnavailable or ErrDesktopNotReady.
type ComputerUse struct{ s *Sandbox }

// ComputerUse returns a concurrency-safe desktop automation namespace.
func (s *Sandbox) ComputerUse() *ComputerUse { return &ComputerUse{s: s} }

// Addons returns diagnostics and controls for the addon's individual processes.
func (c *ComputerUse) Addons() *ComputerUseAddons { return &ComputerUseAddons{s: c.s} }

// Mouse returns desktop pointer controls.
func (c *ComputerUse) Mouse() *ComputerUseMouse { return &ComputerUseMouse{s: c.s} }

// Keyboard returns desktop keyboard controls.
func (c *ComputerUse) Keyboard() *ComputerUseKeyboard { return &ComputerUseKeyboard{s: c.s} }

// Screen returns screenshot capture.
func (c *ComputerUse) Screen() *ComputerUseScreen { return &ComputerUseScreen{s: c.s} }

// Display returns desktop display and window information.
func (c *ComputerUse) Display() *ComputerUseDisplays { return &ComputerUseDisplays{s: c.s} }

// A11y returns accessibility tree inspection and controls.
func (c *ComputerUse) A11y() *ComputerUseA11y { return &ComputerUseA11y{s: c.s} }

// Recording returns desktop recording operations.
func (c *ComputerUse) Recording() *ComputerUseRecordings {
	return &ComputerUseRecordings{s: c.s}
}

// ComputerUseResolution is a desktop size in pixels (1024x768, 1280x720 or 1920x1080).
type ComputerUseResolution struct {
	// Width is the horizontal pixel count.
	Width int `json:"width"`
	// Height is the vertical pixel count.
	Height int `json:"height"`
}

// ComputerUseStartOptions selects the persistent desktop resolution.
type ComputerUseStartOptions struct {
	// Resolution defaults to the last started size, initially 1280x720.
	// The server validates supported sizes. Only Linux supports this option.
	Resolution *ComputerUseResolution `json:"resolution,omitempty"`
}

// Start starts the desktop. Changing a running desktop's resolution returns
// ErrConflict; use Restart instead. Nil options retain the previous resolution.
func (c *ComputerUse) Start(ctx context.Context, opts *ComputerUseStartOptions) (*ComputerUseLifecycleResult, error) {
	spec := toolboxRequest{Method: http.MethodPost, Path: "/computeruse/start"}
	if opts != nil && opts.Resolution != nil {
		spec.Body = opts
	}
	raw, err := desktopRead(ctx, c.s, spec)
	if err != nil {
		return nil, err
	}
	return parseDesktopLifecycle(raw)
}

// Stop stops the desktop addon processes.
func (c *ComputerUse) Stop(ctx context.Context) (*ComputerUseLifecycleResult, error) {
	raw, err := desktopRead(ctx, c.s, toolboxRequest{Method: http.MethodPost, Path: "/computeruse/stop"})
	if err != nil {
		return nil, err
	}
	return parseDesktopLifecycle(raw)
}

// Restart restarts the desktop at its current size, or the supplied resolution
// (Linux only), and returns only after the desktop is ready.
func (c *ComputerUse) Restart(
	ctx context.Context, opts *ComputerUseStartOptions,
) (*ComputerUseRestartResult, error) {
	spec := toolboxRequest{Method: http.MethodPost, Path: "/computeruse/restart"}
	if opts != nil && opts.Resolution != nil {
		spec.Body = opts
	}
	raw, err := desktopRead(ctx, c.s, spec)
	if err != nil {
		return nil, err
	}
	return parseDesktopRestart(raw)
}

// Status observes desktop readiness, including the cause of a failed session.
func (c *ComputerUse) Status(ctx context.Context) (*ComputerUseStatus, error) {
	raw, err := desktopRead(ctx, c.s, toolboxRequest{Path: "/computeruse/status"})
	if err != nil {
		return nil, err
	}
	return parseDesktopStatus(raw)
}

func desktopRead(ctx context.Context, sandbox *Sandbox, spec toolboxRequest) (JSONValue, error) {
	var raw JSONValue
	spec.Result = &raw
	if err := sandbox.toolbox().do(ctx, spec); err != nil {
		return nil, err
	}
	return raw, nil
}
