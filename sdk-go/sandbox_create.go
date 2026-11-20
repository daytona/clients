// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "context"

// Create creates a Linux sandbox from an image name or ID. Empty image selects
// the platform default; whitespace-only images return ErrBadRequest.
// Once accepted, SDK wait failures return the last observed handle, whose State
// may not be Started. Caller cancellation remains an error. A lost POST response
// returns SandboxCreateConnectionError or SandboxCreateRequestTimeoutError,
// carrying the idempotency key to reuse with identical options.
func (c *Client) Create(ctx context.Context, image string, opts *CreateOptions) (*Sandbox, error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	options := CreateOptions{}
	if opts != nil {
		options = *opts
	}
	if image != "" && createTrim(image) == "" {
		return nil, badRequest("image must be a non-empty string")
	}
	prepared, err := prepareCreateCommon(CreateFromSnapshotOptions{
		Name: options.Name, Labels: options.Labels, Ports: options.Ports,
		LifecyclePolicy: options.LifecyclePolicy, Region: options.Region,
		Wait: options.Wait, CreateTimeout: options.CreateTimeout, IdempotencyKey: options.IdempotencyKey,
	})
	if err != nil {
		return nil, err
	}
	if err = lifecyclePolicyClassSupport(options.LifecyclePolicy, SandboxInfo{Class: SandboxClassLinux}); err != nil {
		return nil, err
	}
	switch options.CPUArch {
	case "", "amd64", "arm64":
	default:
		return nil, badRequest("cpuArch must be amd64 or arm64")
	}
	if err = prepareCreateAttachments(&prepared.body, &options); err != nil {
		return nil, err
	}
	if err = prepareLinuxResources(&prepared.body, options.Resources); err != nil {
		return nil, err
	}
	prepared.path, prepared.class = "/sandboxes", SandboxClassLinux
	prepared.body.Image = createTrim(image)
	prepared.body.CPUArch, prepared.body.CPUType = options.CPUArch, options.CPUType
	prepared.body.User, prepared.body.Spot = options.User, clonePointer(options.Spot)
	return c.submitCreate(ctx, prepared)
}

// CreateFromSnapshot restores a Linux sandbox from a snapshot name or ID.
// Snapshot cannot be blank. Wait and uncertain-request errors behave as on Create;
// resource sizing is carried by the snapshot.
func (c *Client) CreateFromSnapshot(
	ctx context.Context, snapshot string, opts *CreateFromSnapshotOptions,
) (*Sandbox, error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	if createTrim(snapshot) == "" {
		return nil, badRequest("snapshot must be a non-empty string")
	}
	options := CreateFromSnapshotOptions{}
	if opts != nil {
		options = *opts
	}
	prepared, err := prepareCreateCommon(options)
	if err != nil {
		return nil, err
	}
	if err = lifecyclePolicyClassSupport(options.LifecyclePolicy, SandboxInfo{Class: SandboxClassLinux}); err != nil {
		return nil, err
	}
	prepared.path, prepared.class = "/sandboxes/from-snapshot", SandboxClassLinux
	prepared.body.Snapshot = createTrim(snapshot)
	return c.submitCreate(ctx, prepared)
}
