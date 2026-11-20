// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestGitOptions_whenInvalidNameGoFields(t *testing.T) {
	for _, test := range []struct {
		field string
		call  func(context.Context, *Git) error
	}{
		{"Depth", func(ctx context.Context, g *Git) error {
			return g.Clone(ctx, "url", "/repo", &GitCloneOptions{Depth: -1})
		}},
		{"Timeout", func(ctx context.Context, g *Git) error {
			return g.Pull(ctx, "/repo", &GitCredentialOptions{Timeout: -time.Second})
		}},
		{"Scope", func(ctx context.Context, g *Git) error {
			_, err := g.GetConfig(ctx, "key", &GitConfigOptions{Scope: "future"})
			return err
		}},
		{"Author", func(ctx context.Context, g *Git) error {
			_, err := g.Commit(ctx, "/repo", "message", &GitCommitOptions{Email: "e"})
			return err
		}},
		{"Email", func(ctx context.Context, g *Git) error {
			_, err := g.Commit(ctx, "/repo", "message", &GitCommitOptions{Author: "a"})
			return err
		}},
	} {
		t.Run(test.field, func(t *testing.T) {
			// Given an invalid option and no routes.
			git, fake := gitTestService(t, nil)
			// When validating the request.
			err := test.call(t.Context(), git)
			// Then the Go field is named before sending anything.
			require.ErrorIs(t, err, ErrBadRequest)
			var sdk *Error
			require.ErrorAs(t, err, &sdk)
			require.Contains(t, sdk.Message, test.field+" ")
			require.Empty(t, fake.requests())
		})
	}
}
