// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"iter"

	"github.com/daytona/clients/sdk-go/pkg/options"
)

// V1Client is the versioned Daytona SDK 1.x client surface.
//
// V1Client provides methods to create, retrieve, list, and manage sandboxes. It handles
// authentication, API communication, and provides access to services like Volume and Snapshot
// management.
//
// Obtain a V1Client from [Client.V1]:
//
//	client, err := daytona.NewClient()
//	if err != nil {
//	    log.Fatal(err)
//	}
//	v1 := client.V1()
//
// The V1Client is safe for concurrent use by multiple goroutines.
type V1Client struct {
	client *Client

	// Otel holds OpenTelemetry state; nil when OTel is disabled.
	Otel *otelState

	// Volume provides methods for managing persistent volumes.
	Volume *VolumeService

	// Snapshot provides methods for managing sandbox snapshots.
	Snapshot *SnapshotService

	// Secret provides methods for managing organization secrets.
	Secret *SecretService

	// WarmPool provides methods for managing warm pools of ready sandboxes.
	WarmPool *WarmPoolService
}

// V1 returns the cached v1 client.
func (c *Client) V1() *V1Client {
	if c == nil {
		return nil
	}
	// NewClient sets v1; the lazy path only covers a Client built without it.
	c.v1Once.Do(func() {
		if c.v1 == nil {
			c.v1 = newV1Client(c)
		}
	})
	return c.v1
}

func newV1Client(client *Client) *V1Client {
	if client == nil {
		return nil
	}

	return &V1Client{
		client:   client,
		Otel:     client.Otel,     //nolint:staticcheck // root field must mirror the v1 field for backwards compatibility.
		Volume:   client.Volume,   //nolint:staticcheck // root field must mirror the v1 field for backwards compatibility.
		Snapshot: client.Snapshot, //nolint:staticcheck // root field must mirror the v1 field for backwards compatibility.
		Secret:   client.Secret,   //nolint:staticcheck // root field must mirror the v1 field for backwards compatibility.
		WarmPool: client.WarmPool, //nolint:staticcheck // root field must mirror the v1 field for backwards compatibility.
	}
}

// Close shuts down the client and releases resources.
// When OpenTelemetry is enabled, Close flushes and shuts down the OTel providers.
// It is safe to call Close even when OTel is not enabled.
func (c *Client) Close(ctx context.Context) error {
	return c.close(ctx)
}

// Close shuts down the client and releases resources.
// When OpenTelemetry is enabled, Close flushes and shuts down the OTel providers.
// It is safe to call Close even when OTel is not enabled.
func (c *V1Client) Close(ctx context.Context) error {
	return c.client.close(ctx)
}

// Create creates a new sandbox with the specified parameters.
//
// The params argument accepts either [types.SnapshotParams] to create from a snapshot,
// or [types.ImageParams] to create from a Docker image (a pointer to either is also accepted).
// Pass nil to create a sandbox with default parameters:
//
//	// Create from a snapshot
//	sandbox, err := v1.Create(ctx, types.SnapshotParams{
//	    Snapshot: "my-snapshot",
//	    SandboxBaseParams: types.SandboxBaseParams{
//	        Name: "my-sandbox",
//	    },
//	})
//
//	// Create from a Docker image
//	sandbox, err := v1.Create(ctx, types.ImageParams{
//	    Image: "python:3.11",
//	    Resources: &types.Resources{
//	        CPU:    2,
//	        Memory: 4096,
//	    },
//	})
//
// By default, Create waits for the sandbox to reach the started state before returning.
// Pass options.WithWaitForStart(false) (see [options.WithWaitForStart]) to return immediately after creation.
//
// Optional parameters can be configured using functional options:
//   - [options.WithTimeout]: Set maximum wait time for creation
//   - [options.WithWaitForStart]: Control whether to wait for started state
//   - [options.WithLogChannel]: Receive build logs during image builds
//
// Returns the created [Sandbox] or an error if creation fails.
func (c *V1Client) Create(ctx context.Context, params any, opts ...func(*options.CreateSandbox)) (*Sandbox, error) {
	return withInstrumentation(ctx, c.Otel, "Client", "Create", func(ctx context.Context) (*Sandbox, error) {
		return c.client.doCreate(ctx, params, opts...)
	})
}

// Get retrieves an existing sandbox by its ID or name.
//
// The sandboxIDOrName parameter accepts either the sandbox's unique ID or its
// human-readable name. If a sandbox with the given identifier is not found,
// a [*github.com/daytona/clients/sdk-go/pkg/errors.DaytonaError] matching
// [github.com/daytona/clients/sdk-go/pkg/errors.ErrNotFound] is returned.
//
// Example:
//
//	sandbox, err := v1.Get(ctx, "my-sandbox")
//	if err != nil {
//	    if errors.Is(err, sdkerrors.ErrNotFound) {
//	        log.Println("Sandbox not found")
//	    }
//	    return err
//	}
func (c *V1Client) Get(ctx context.Context, sandboxIDOrName string) (*Sandbox, error) {
	return withInstrumentation(ctx, c.Otel, "Client", "Get", func(ctx context.Context) (*Sandbox, error) {
		return c.client.doGet(ctx, sandboxIDOrName)
	})
}

// List returns an iterator over Sandboxes matching the given query, following
// the [database/sql.Rows] / [bufio.Scanner] iterator pattern.
//
// For Go 1.23+ range-over-func consumers, see [V1Client.ListSeq].
//
// Example:
//
//	iter := v1.List(ctx, &ListSandboxesQuery{Labels: map[string]string{"env": "dev"}})
//	for iter.Next() {
//	    fmt.Println(iter.Value().ID)
//	}
//	if err := iter.Err(); err != nil {
//	    log.Fatal(err)
//	}
func (c *V1Client) List(ctx context.Context, query *ListSandboxesQuery) *SandboxIterator {
	return c.client.list(ctx, query)
}

// ListSeq returns a Go 1.23+ range-over-func iterator over Sandboxes matching
// the given query. Each yielded pair is (sandbox, error); a non-nil error
// terminates iteration and the consumer should break out of the range.
//
// Example:
//
//	for sandbox, err := range v1.ListSeq(ctx, &ListSandboxesQuery{...}) {
//	    if err != nil {
//	        log.Fatal(err)
//	    }
//	    fmt.Println(sandbox.ID)
//	}
func (c *V1Client) ListSeq(ctx context.Context, query *ListSandboxesQuery) iter.Seq2[*Sandbox, error] {
	return c.client.listSeq(ctx, query)
}

// List returns an iterator over Sandboxes matching the given query, following
// the [database/sql.Rows] / [bufio.Scanner] iterator pattern.
//
// For Go 1.23+ range-over-func consumers, see [V1Client.ListSeq].
//
// Example:
//
//	v1 := client.V1()
//	iter := v1.List(ctx, &ListSandboxesQuery{Labels: map[string]string{"env": "dev"}})
//	for iter.Next() {
//	    fmt.Println(iter.Value().ID)
//	}
//	if err := iter.Err(); err != nil {
//	    log.Fatal(err)
//	}
//
// Deprecated: Use client.V1().List instead. The root form is removed in SDK 2.x.
func (c *Client) List(ctx context.Context, query *ListSandboxesQuery) *SandboxIterator {
	warnDeprecatedRootMember("List")
	return c.V1().List(ctx, query)
}

// ListSeq returns a Go 1.23+ range-over-func iterator over Sandboxes matching
// the given query. Each yielded pair is (sandbox, error); a non-nil error
// terminates iteration and the consumer should break out of the range.
//
// Example:
//
//	v1 := client.V1()
//	for sandbox, err := range v1.ListSeq(ctx, &ListSandboxesQuery{...}) {
//	    if err != nil {
//	        log.Fatal(err)
//	    }
//	    fmt.Println(sandbox.ID)
//	}
//
// Deprecated: Use client.V1().ListSeq instead. The root form is removed in SDK 2.x.
func (c *Client) ListSeq(ctx context.Context, query *ListSandboxesQuery) iter.Seq2[*Sandbox, error] {
	warnDeprecatedRootMember("ListSeq")
	return c.V1().ListSeq(ctx, query)
}
