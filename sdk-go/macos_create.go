// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"unicode/utf8"
)

// CreateMacOS creates a macOS sandbox from an image name or ID. Image cannot be
// blank or invalid UTF-8. Wait and uncertain-request errors behave as on Create. macOS CPU and
// sizing minima are checked locally; reservation availability is decided by the API.
// Once accepted, a wait that times out, fails a read, or ends in a terminal
// state returns a nil error; check the returned sandbox's State() or Info().
// Caller cancellation and deadline expiry remain errors.
func (c *Client) CreateMacOS(ctx context.Context, image string, opts *CreateMacOSOptions) (*Sandbox, error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	if createTrim(image) == "" {
		return nil, badRequest("image must be a non-empty string")
	}
	if !utf8.ValidString(image) {
		return nil, badRequest("image must be valid UTF-8")
	}
	options := CreateMacOSOptions{}
	if opts != nil {
		options = *opts
	}
	if options.AutoRenew != nil && options.Reservation != "new" {
		return nil, badRequest("AutoRenew requires Reservation new")
	}
	prepared, err := prepareCreateCommon(CreateFromSnapshotOptions{
		Name: options.Name, Labels: options.Labels, Ports: options.Ports,
		LifecyclePolicy: options.LifecyclePolicy, Region: options.Region,
		Wait: options.Wait, CreateTimeout: options.CreateTimeout, IdempotencyKey: options.IdempotencyKey,
	})
	if err != nil {
		return nil, err
	}
	persistent := options.Persistent != nil && *options.Persistent
	if err = lifecyclePolicyClassSupport(options.LifecyclePolicy, SandboxInfo{
		Class: SandboxClassMacOS, Persistent: &persistent,
	}); err != nil {
		return nil, err
	}
	if err = prepareCreateAttachments(&prepared.body, &CreateOptions{
		Env: options.Env, Volumes: options.Volumes, Secrets: options.Secrets,
		NetworkPolicy: options.NetworkPolicy, ClearNetworkPolicy: options.ClearNetworkPolicy,
	}); err != nil {
		return nil, err
	}
	if err = prepareMacOSResources(&prepared.body, options.Resources); err != nil {
		return nil, err
	}
	prepared.path, prepared.class = "/sandboxes/macos", SandboxClassMacOS
	prepared.body.Image = createTrim(image)
	prepared.body.Persistent = clonePointer(options.Persistent)
	prepared.body.AutoRenew = clonePointer(options.AutoRenew)
	prepared.body.Reservation = options.Reservation
	return c.submitCreate(ctx, prepared)
}
