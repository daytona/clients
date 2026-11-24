// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestGet_whenCancellationCauseHasMultipleLines_preservesCauseOnOneLine(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	first, second := errors.New("user aborted"), errors.New("cleanup\nfailed")
	cause := errors.Join(first, second)
	ctx, cancel := context.WithCancelCause(t.Context())
	cancel(cause)

	// When
	_, err := client.Get(ctx, "sbx", nil)

	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.ErrorIs(t, err, first)
	require.ErrorIs(t, err, second)
	require.ErrorContains(t, err, "user aborted; cleanup; failed")
	require.NotContains(t, err.Error(), "\n")
	require.Empty(t, fake.requests())
}
