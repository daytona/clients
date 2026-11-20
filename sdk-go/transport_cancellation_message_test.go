// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestGet_whenCancelledWithCause_reportsSingleLine(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	cause := errors.New("user aborted")
	ctx, cancel := context.WithCancelCause(t.Context())
	cancel(cause)

	// When
	_, err := client.Get(ctx, "sbx", nil)

	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.ErrorIs(t, err, cause)
	require.ErrorContains(t, err, cause.Error())
	require.NotContains(t, err.Error(), "\n")
	require.Empty(t, fake.requests())
}
