// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"iter"
	"math"
	"net/http"
	"net/url"
	"time"
	"unicode/utf8"
)

// Volumes manages project-scoped volumes. Obtain it from Client.Volumes.
type Volumes struct{ client *Client }

// Volumes returns the project's volume service without performing I/O.
func (c *Client) Volumes() *Volumes { return &Volumes{client: c} }

// ListVolumesOptions selects one page of volumes.
type ListVolumesOptions struct {
	// Cursor is the opaque continuation cursor.
	Cursor string
	// Limit is 1–200; zero uses the server default.
	Limit int
}

// GetVolumeOptions controls volume lookup.
type GetVolumeOptions struct {
	// Create creates a volume on ErrNotFound unless IDOnly is true.
	Create bool
	// IDOnly disables name lookup and creation.
	IDOnly bool
}

// CreateVolumeOptions controls volume creation.
type CreateVolumeOptions struct {
	// Type is disk or bucket; empty lets the server choose (currently disk).
	Type VolumeType
	// SizeGiB is disk capacity in GiB, up to 2147483647; zero uses the server default.
	SizeGiB int64
}

// DeleteVolumeOptions controls volume deletion.
type DeleteVolumeOptions struct {
	// IDOnly disables name lookup.
	IDOnly bool
}

// List returns exactly one volume page, preferring eventually consistent search
// and falling back to the list route only on 404, SEARCH_NOT_CONFIGURED or a
// non-page answer from an older API. Invalid pagination returns ErrBadRequest.
func (v *Volumes) List(ctx context.Context, opts *ListVolumesOptions) (*Page[Volume], error) {
	options := ListVolumesOptions{}
	if opts != nil {
		options = *opts
	}
	query, err := pageQuery(options.Cursor, options.Limit)
	if err != nil {
		return nil, err
	}
	reader := resourceListReader{artifactListReader{v.client, "/volumes", &v.client.volumeSearchFallback}, "volumes"}
	raw, err := reader.read(ctx, query)
	if err != nil {
		return nil, err
	}
	return resourcePage(raw, "volumes", parseVolume)
}

// ListAll lazily traverses volume pages, stopping on cancellation, error, or a
// repeated cursor. Options are copied before iteration starts.
func (v *Volumes) ListAll(ctx context.Context, opts *ListVolumesOptions) iter.Seq2[Volume, error] {
	options := ListVolumesOptions{}
	if opts != nil {
		options = *opts
	}
	return paginate(ctx, options.Cursor, func(ctx context.Context, cursor string) (*Page[Volume], error) {
		pageOptions := options
		pageOptions.Cursor = cursor
		return v.List(ctx, &pageOptions)
	})
}

// volumeConflictRereadDelays are the pauses before each read of a volume
// another caller created first (as the Python and TypeScript SDKs).
var volumeConflictRereadDelays = []time.Duration{
	0, 50 * time.Millisecond, 100 * time.Millisecond, 200 * time.Millisecond, 400 * time.Millisecond,
}

// Get fetches a volume by ID or name. Create requests creation on ErrNotFound;
// IDOnly always suppresses creation. When another caller creates that name
// first (the create answers 409 CONFLICT), the volume is read again by name, a
// few times over about a second, and returned; the conflict is returned if it
// never becomes readable. Other server errors are preserved.
func (v *Volumes) Get(ctx context.Context, nameOrID string, opts *GetVolumeOptions) (*Volume, error) {
	segment, err := resourceSegment(nameOrID, "volume")
	if err != nil {
		return nil, err
	}
	options := GetVolumeOptions{}
	if opts != nil {
		options = *opts
	}
	result, err := v.read(ctx, segment, options.IDOnly)
	if err == nil || !options.Create || options.IDOnly || !errors.Is(err, ErrNotFound) {
		return result, err
	}
	created, createErr := v.Create(ctx, nameOrID, nil)
	var conflict *Error
	// Only the name conflict a racing create causes; any other 409 is returned.
	if createErr == nil || !errors.As(createErr, &conflict) || conflict.StatusCode != http.StatusConflict ||
		conflict.Code != "CONFLICT" {
		return created, createErr
	}
	for _, delay := range volumeConflictRereadDelays {
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, transportError(ctx, ctx.Err())
		case <-timer.C:
		}
		result, err = v.read(ctx, segment, false)
		if !errors.Is(err, ErrNotFound) {
			return result, err
		}
	}
	return nil, createErr
}

func (v *Volumes) read(ctx context.Context, segment string, idOnly bool) (*Volume, error) {
	var raw JSONValue
	if err := v.client.api().do(ctx, apiRequest{
		Path: "/volumes/" + segment, Query: volumeRefQuery(idOnly), Result: &raw,
	}); err != nil {
		return nil, err
	}
	result, err := parseVolume(raw)
	if err != nil {
		return nil, err
	}
	return &result, nil
}

// Create creates a named volume. An omitted type uses the server default (disk).
// Invalid names, types or sizes return ErrBadRequest before any request.
func (v *Volumes) Create(ctx context.Context, name string, opts *CreateVolumeOptions) (*Volume, error) {
	if builderTrim(name) == "" || !utf8.ValidString(name) {
		return nil, badRequest("name must be a non-empty UTF-8 string")
	}
	if err := refuseSearchName(name, "volume"); err != nil {
		return nil, err
	}
	options := CreateVolumeOptions{}
	if opts != nil {
		options = *opts
	}
	switch options.Type {
	case "", VolumeTypeDisk, VolumeTypeBucket:
	case VolumeTypeUnknown:
		fallthrough
	default:
		return nil, badRequest("Type must be disk or bucket")
	}
	if options.SizeGiB < 0 || options.SizeGiB > math.MaxInt32 {
		return nil, badRequest("SizeGiB must be a positive integer up to 2147483647, or zero for default")
	}
	body := struct {
		Name    string     `json:"name"`
		Type    VolumeType `json:"type,omitempty"`
		SizeGiB int64      `json:"size_gib,omitempty"`
	}{name, options.Type, options.SizeGiB}
	var raw JSONValue
	if err := v.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: "/volumes", Body: body, Result: &raw,
	}); err != nil {
		return nil, err
	}
	result, err := parseVolume(raw)
	if err != nil {
		return nil, err
	}
	if builderTrim(result.ID) == "" {
		return nil, badRequest("volume response is missing its id")
	}
	return &result, nil
}

// Delete requests deletion by ID or name and returns when accepted, without
// waiting for final commits or deletion to finish. ErrNotFound is preserved.
func (v *Volumes) Delete(ctx context.Context, nameOrID string, opts *DeleteVolumeOptions) error {
	segment, err := resourceSegment(nameOrID, "volume")
	if err != nil {
		return err
	}
	idOnly := opts != nil && opts.IDOnly
	return v.client.api().do(ctx, apiRequest{
		Method: http.MethodDelete, Path: "/volumes/" + segment, Query: volumeRefQuery(idOnly),
	})
}

func volumeRefQuery(idOnly bool) url.Values {
	if idOnly {
		return url.Values{"id_only": {"true"}}
	}
	return nil
}

// String returns a credential-free diagnostic name.
func (v *Volumes) String() string { return "daytona.Volumes" }

// GoString returns a credential-free diagnostic name.
func (v *Volumes) GoString() string { return v.String() }
