// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"time"
	"unicode/utf8"
)

// CaptureImageOptions names a filesystem capture or configures idle retention.
type CaptureImageOptions struct {
	// Name makes the capture permanent; empty creates an anonymous image.
	Name string
	// IdleExpiry is positive whole seconds, WaitForever for the server maximum,
	// or zero to use the server default. It cannot be combined with Name.
	IdleExpiry time.Duration
}

// CaptureImage captures the sandbox filesystem as an image without waiting
// for readiness. Linux sandboxes keep running. macOS requires a stopped,
// persistent sandbox; ErrDiskNotReady means the disk is not ready yet.
// Combining Name and IdleExpiry returns ErrBadRequest before any request.
// A sandbox still starting or stopping is retried for up to 30 seconds on a
// transition-in-progress refusal; other failures are returned immediately.
// Caller context and client closure apply.
func (s *Sandbox) CaptureImage(ctx context.Context, opts *CaptureImageOptions) (*Image, error) {
	options := CaptureImageOptions{}
	if opts != nil {
		options = *opts
	}
	body, err := imageOptions(BuildImageOptions{Name: options.Name, IdleExpiry: options.IdleExpiry})
	if err != nil {
		return nil, err
	}
	segment, err := pathSegment(s.ID())
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	err = s.client.retrySandboxTransition(ctx, apiRequest{
		Method: http.MethodPost, Path: "/sandboxes/" + segment + "/images", Body: body, Result: &raw,
	})
	if err != nil {
		return nil, err
	}
	id, err := imageAcknowledgedID(raw)
	if err != nil {
		return nil, err
	}
	return s.client.Images().Get(ctx, id, nil)
}

// CreateSnapshot captures memory and disk without waiting for readiness.
// Name must be non-blank, valid UTF-8; otherwise it returns ErrBadRequest
// before any request. Both sandbox-write and snapshot-write permissions are
// required; use Snapshots.Get with Wait to observe completion.
// A sandbox still starting or stopping is retried for up to 30 seconds on a
// transition-in-progress refusal; other conflicts are not retried.
// Caller context and client closure apply.
func (s *Sandbox) CreateSnapshot(ctx context.Context, name string) (*Snapshot, error) {
	if createTrim(name) == "" || !utf8.ValidString(name) {
		return nil, badRequest("name must be a non-empty UTF-8 string")
	}
	segment, err := pathSegment(s.ID())
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	err = s.client.retrySandboxTransition(ctx, apiRequest{
		Method: http.MethodPost, Path: "/sandboxes/" + segment + "/snapshots",
		Body: struct {
			Name string `json:"name"`
		}{name}, Result: &raw,
	})
	if err != nil {
		return nil, err
	}
	id, err := imageAcknowledgedID(raw)
	if err != nil {
		return nil, err
	}
	return s.client.Snapshots().Get(ctx, id, nil)
}
