// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestGet_whenCallerCancelsWithCause_preservesCancellationAndCause(t *testing.T) {
	// Given
	arrived := make(chan struct{})
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx": func(_ http.ResponseWriter, r *http.Request) {
			close(arrived)
			<-r.Context().Done()
		},
	})
	client := newTestClient(t, fake.Server)
	cause := errors.New("caller stopped the operation")
	ctx, cancel := context.WithCancelCause(t.Context())
	defer cancel(nil)
	result := make(chan error, 1)
	// When
	go func() {
		_, err := client.Get(ctx, "sbx", nil)
		result <- err
	}()
	<-arrived
	cancel(cause)
	err := <-result
	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.ErrorIs(t, err, cause)
}

func TestTransportError_whenDeadlineIsCancellationCause_preservesTimeoutCategory(t *testing.T) {
	// Given: request scopes signal timeout through a cancellation cause.
	ctx, cancel := context.WithCancelCause(t.Context())
	cancel(context.DeadlineExceeded)
	// When
	err := transportError(ctx, ctx.Err())
	// Then
	require.ErrorIs(t, err, ErrClientTimeout)
	require.ErrorIs(t, err, context.Canceled)
	require.ErrorIs(t, err, context.DeadlineExceeded)
}
