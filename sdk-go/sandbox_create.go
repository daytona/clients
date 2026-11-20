// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"unicode/utf8"
)

// Create creates a Linux sandbox from an image of this project, by name or
// img_ ID. Empty image boots the platform default image (Python 3 included).
// A registry reference such as "python:3.12" is not a create source: import it
// once with client.Images().Pull and create from the returned image's name or
// ID (an unknown image returns ErrImageNotFound). Whitespace-only images return
// ErrBadRequest, as do invalid UTF-8 image identifiers.
// Once accepted, a wait that times out, fails a read, or ends in a terminal
// state returns the last observed handle with a nil error; check State() or
// Info() before using it. If the server accepted the request, the returned
// *Sandbox is non-nil even when err is non-nil: delete it or keep it. Caller cancellation
// or deadline expiry remains reachable through errors.Is. A lost POST response
// returns SandboxCreateConnectionError or SandboxCreateRequestTimeoutError,
// carrying the idempotency key to reuse with identical options, including when
// the caller's context interrupted the POST.
// SandboxCreateConnectionError can also report a failure before the POST was
// sent, such as bearer exchange failure or client closure. Retrying with the
// carried key and identical options is safe in either case.
func (c *Client) Create(ctx context.Context, image string, opts *CreateOptions) (*Sandbox, error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	options := CreateOptions{}
	if opts != nil {
		options = *opts
	}
	prepared, err := prepareLinuxCreate(image, options)
	if err != nil {
		return nil, err
	}
	return c.submitCreate(ctx, prepared)
}

func prepareLinuxCreate(image string, options CreateOptions) (preparedCreate, error) {
	if image != "" && createTrim(image) == "" {
		return preparedCreate{}, badRequest("image must be a non-empty string")
	}
	if !utf8.ValidString(image) {
		return preparedCreate{}, badRequest("image must be valid UTF-8")
	}
	prepared, err := prepareCreateCommon(CreateFromSnapshotOptions{
		Name: options.Name, Labels: options.Labels, Ports: options.Ports,
		LifecyclePolicy: options.LifecyclePolicy, Region: options.Region,
		Wait: options.Wait, CreateTimeout: options.CreateTimeout, IdempotencyKey: options.IdempotencyKey,
	})
	if err != nil {
		return prepared, err
	}
	if err = lifecyclePolicyClassSupport(options.LifecyclePolicy, SandboxInfo{Class: SandboxClassLinux}); err != nil {
		return prepared, err
	}
	switch options.CPUArch {
	case "", CPUArchAMD64, CPUArchARM64:
	default:
		return prepared, badRequest("CPUArch must be amd64 or arm64")
	}
	if err = prepareCreateAttachments(&prepared.body, &options); err != nil {
		return prepared, err
	}
	if err = prepareLinuxResources(&prepared.body, options.Resources); err != nil {
		return prepared, err
	}
	prepared.path, prepared.class = "/sandboxes", SandboxClassLinux
	prepared.body.Image = createTrim(image)
	prepared.body.CPUArch, prepared.body.CPUType = options.CPUArch, options.CPUType
	prepared.body.User, prepared.body.Spot = options.User, clonePointer(options.Spot)
	return prepared, nil
}

// CreateFromSnapshot restores a Linux sandbox from a snapshot name or ID.
// Snapshot cannot be blank or invalid UTF-8. Wait and uncertain-request errors behave as on Create;
// resource sizing is carried by the snapshot.
// Once accepted, a wait that times out, fails a read, or ends in a terminal
// state returns a nil error; check the returned sandbox's State() or Info().
// Caller cancellation and deadline expiry remain errors.
func (c *Client) CreateFromSnapshot(
	ctx context.Context, snapshot string, opts *CreateFromSnapshotOptions,
) (*Sandbox, error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	if createTrim(snapshot) == "" {
		return nil, badRequest("snapshot must be a non-empty string")
	}
	if !utf8.ValidString(snapshot) {
		return nil, badRequest("snapshot must be valid UTF-8")
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
