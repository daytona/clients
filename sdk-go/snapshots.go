// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"iter"
	"net/http"
)

// Snapshots manages saved sandbox memory and disk. Obtain it with
// Client.Snapshots. Its methods are safe for concurrent use.
type Snapshots struct{ client *Client }

// Snapshots returns the client's snapshot service.
func (c *Client) Snapshots() *Snapshots { return &Snapshots{client: c} }

// ListSnapshotsOptions filters one snapshot page. Zero values use defaults.
type ListSnapshotsOptions struct {
	// Cursor is the opaque continuation cursor.
	Cursor string
	// Limit is 1–200; zero uses the server default.
	Limit int
	// Name filters by snapshot name.
	Name string
	// SandboxID filters by the captured sandbox.
	SandboxID string
}

// List returns one page of snapshot values, preferring eventually consistent
// search and falling back only on 404 or SEARCH_NOT_CONFIGURED.
func (s *Snapshots) List(ctx context.Context, opts *ListSnapshotsOptions) (*Page[Snapshot], error) {
	options := ListSnapshotsOptions{}
	if opts != nil {
		options = *opts
	}
	query, err := pageQuery(options.Cursor, options.Limit)
	if err != nil {
		return nil, err
	}
	if err = artifactFilters(query, options.Name, options.SandboxID); err != nil {
		return nil, err
	}
	reader := artifactListReader{s.client, "/snapshots", &s.client.snapshotSearchFallback}
	raw, err := reader.read(ctx, query)
	if err != nil {
		return nil, err
	}
	return artifactPage(raw, "snapshots", func(raw JSONValue) (Snapshot, error) {
		snapshot, parseErr := parseSnapshot(raw)
		if parseErr != nil {
			return Snapshot{}, parseErr
		}
		return *snapshot, nil
	})
}

// ListAll lazily visits snapshot pages until exhaustion, a repeated cursor,
// an error, cancellation or an early consumer break.
func (s *Snapshots) ListAll(ctx context.Context, opts *ListSnapshotsOptions) iter.Seq2[Snapshot, error] {
	options := ListSnapshotsOptions{}
	if opts != nil {
		options = *opts
	}
	return paginate(ctx, options.Cursor, func(ctx context.Context, cursor string) (*Page[Snapshot], error) {
		pageOptions := options
		pageOptions.Cursor = cursor
		return s.List(ctx, &pageOptions)
	})
}

// Get reads a snapshot by ID or name. "search" must be read by ID instead.
// A failed/error snapshot is returned for inspection, even when waiting.
func (s *Snapshots) Get(ctx context.Context, id string, opts *GetArtifactOptions) (*Snapshot, error) {
	path, err := artifactPath("/snapshots", id)
	if err != nil {
		return nil, err
	}
	reader := artifactReader[*Snapshot]{s.client, path, parseSnapshot}
	return reader.get(ctx, opts)
}

// Delete deletes a snapshot by ID or name. Missing snapshots return ErrNotFound.
func (s *Snapshots) Delete(ctx context.Context, id string) error {
	segment, err := pathSegment(id)
	if err != nil {
		return err
	}
	return s.client.api().do(ctx, apiRequest{Method: http.MethodDelete, Path: "/snapshots/" + segment})
}
