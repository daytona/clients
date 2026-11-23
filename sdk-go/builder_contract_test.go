// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"

	"go.daytona.com/sdk/v2/internal/buildcontext"
)

func TestBuilderDockerfileDoesNotReadSources(t *testing.T) {
	// Given a declaration whose local source was subsequently removed.
	filename := filepath.Join(t.TempDir(), "file")
	require.NoError(t, os.WriteFile(filename, []byte("content"), 0o600))
	builder := NewImageBuilder("alpine").AddLocalFile(filename, "/input")
	require.NoError(t, os.Remove(filename))
	// When compiled, then rendering uses only declaration metadata.
	text, err := builder.Dockerfile()
	require.NoError(t, err)
	require.Equal(t, "FROM alpine\nCOPY [\".daytona-context/1/file\", \"/input\"]\n", text)
}

func TestBuilderPreparePreservesCancellation(t *testing.T) {
	for _, cause := range []error{context.Canceled, context.DeadlineExceeded} {
		t.Run(cause.Error(), func(t *testing.T) {
			// Given a valid local recipe and a cancelled build operation.
			filename := filepath.Join(t.TempDir(), "file")
			require.NoError(t, os.WriteFile(filename, []byte("content"), 0o600))
			builder := NewImageBuilder("alpine").AddLocalFile(filename, "/input")
			ctx, cancel := context.WithCancelCause(t.Context())
			cancel(cause)
			// When prepared, then both the SDK type and original cause survive.
			result, err := builder.prepare(ctx)
			require.Nil(t, result)
			require.ErrorIs(t, err, cause)
			var sdk *Error
			require.ErrorAs(t, err, &sdk)
			require.Equal(t, "SDK", sdk.Source)
		})
	}
}

func TestBuilderContextErrorBoundary(t *testing.T) {
	for _, tc := range []struct {
		name  string
		input error
		want  error
	}{
		{"invalid", &buildcontext.Error{Invalid: true, Message: "invalid context"}, ErrBadRequest},
		{"cancelled", &buildcontext.Error{Message: "cancelled", Err: context.Canceled}, context.Canceled},
		{"deadline", &buildcontext.Error{Message: "expired", Err: context.DeadlineExceeded}, context.DeadlineExceeded},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given an internal archive failure.
			// When converted, then the public boundary preserves typed matching.
			err := imageContextError(tc.input)
			require.ErrorIs(t, err, tc.want)
			require.ErrorIs(t, err, tc.input)
			var sdk *Error
			require.ErrorAs(t, err, &sdk)
			require.Equal(t, "SDK", sdk.Source)
		})
	}
}

func TestBuilderConcurrentBranches(t *testing.T) {
	// Given a shared recipe with existing layers and spare slice capacity.
	base := NewImageBuilder("alpine").RunCommands("one", "two", "three")
	for _, value := range []string{"first", "second", "third"} {
		t.Run(value, func(t *testing.T) {
			t.Parallel()
			// When independent branches are compiled concurrently.
			text, err := base.Env("VALUE", value).Dockerfile()
			// Then each branch retains only its own additional layer.
			require.NoError(t, err)
			require.Equal(t, "FROM alpine\nRUN one\nRUN two\nRUN three\nENV VALUE=\""+value+"\"\n", text)
		})
	}
}
