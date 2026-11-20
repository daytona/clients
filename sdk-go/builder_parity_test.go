// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestBuilderSharedText(t *testing.T) {
	var fixtures struct {
		Cases   []builderFixture
		Refused []builderFixture
	}
	builderFixtures(t, "image-builder-parity.json", &fixtures)
	for _, tc := range fixtures.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			// Given a shared recipe.
			builder := builderFromFixture(t, tc, "")
			// When compiled, then text and request-source selection agree.
			dockerfile, err := builder.Dockerfile()
			require.NoError(t, err)
			require.Equal(t, tc.Dockerfile, dockerfile)
			ref, registry := builder.registrySource()
			require.Equal(t, tc.Request.Source != nil, registry)
			if registry {
				require.Equal(t, tc.Request.Source.Ref, ref)
			}
		})
	}
	for _, tc := range fixtures.Refused {
		t.Run(tc.Name, func(t *testing.T) {
			// Given shared invalid input retained during chaining.
			builder := builderFromFixture(t, tc, "")
			// When compiled, then it fails in the SDK hierarchy.
			_, err := builder.Dockerfile()
			require.ErrorIs(t, err, ErrBadRequest)
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.Equal(t, "SDK", failure.Source)
		})
	}
}

func TestBuilderSharedDirectives(t *testing.T) {
	var fixture struct {
		Cases []struct {
			Dockerfile string
			Escape     string
		}
	}
	builderFixtures(t, "dockerfile-escape.json", &fixture)
	for _, tc := range fixture.Cases {
		t.Run(tc.Dockerfile, func(t *testing.T) {
			// Given a shared BuildKit parser-directive case.
			builder := ImageFromDockerfile(tc.Dockerfile).Env("TOKEN", "$HOME")
			// When compiled, then literals are accepted only under backslash.
			dockerfile, err := builder.Dockerfile()
			if tc.Escape != "\\" {
				require.ErrorIs(t, err, ErrBadRequest)
			} else {
				require.NoError(t, err)
				require.True(t, strings.HasSuffix(dockerfile, "ENV TOKEN=\"\\$HOME\"\n"))
			}
		})
	}
}

func TestBuilderSharedContinuations(t *testing.T) {
	var fixture struct {
		Cases []struct {
			Base  string
			Joins bool
		}
	}
	builderFixtures(t, "dockerfile-continuation.json", &fixture)
	for _, tc := range fixture.Cases {
		t.Run(tc.Base, func(t *testing.T) {
			// Given a shared BuildKit continuation case.
			builder := ImageFromDockerfile(tc.Base).Workdir("/app")
			// When compiled, then appended instructions cannot be swallowed.
			dockerfile, err := builder.Dockerfile()
			if tc.Joins {
				require.ErrorIs(t, err, ErrBadRequest)
			} else {
				require.NoError(t, err)
				require.True(t, strings.HasSuffix(dockerfile, "WORKDIR /app\n"))
			}
		})
	}
}

func TestBuilderImmutableInputs(t *testing.T) {
	// Given a base and mutable map/slice inputs.
	base := NewImageBuilder("alpine")
	env := map[string]string{"Z": "old", "A": "first"}
	args := []string{"echo", "original"}
	recipe := base.EnvVars(env).Cmd(args...)
	env["Z"], args[1] = "new", "changed"
	left, right := recipe.Workdir("/left"), recipe.Workdir("/right")
	// When independently compiled, then branches and input snapshots survive.
	for _, tc := range []struct {
		builder *ImageBuilder
		want    string
	}{
		{base, "FROM alpine\n"},
		{recipe, "FROM alpine\nENV A=\"first\"\nENV Z=\"old\"\nCMD [\"echo\", \"original\"]\n"},
		{left, "FROM alpine\nENV A=\"first\"\nENV Z=\"old\"\nCMD [\"echo\", \"original\"]\nWORKDIR /left\n"},
		{right, "FROM alpine\nENV A=\"first\"\nENV Z=\"old\"\nCMD [\"echo\", \"original\"]\nWORKDIR /right\n"},
	} {
		text, err := tc.builder.Dockerfile()
		require.NoError(t, err)
		require.Equal(t, tc.want, text)
	}
}

func TestBuilderInvalidZeroAndArguments(t *testing.T) {
	cases := []*ImageBuilder{
		nil,
		{},
		NewImageBuilder(""), ImageFromDockerfile(" "), NewImageBuilder("alpine").RunCommands(),
		NewImageBuilder("alpine").PipInstall(), NewImageBuilder("alpine").Env("A", ""),
		NewImageBuilder("alpine").Entrypoint(), NewImageBuilder("alpine").Cmd(" "),
		NewImageBuilder("alpine").AddLocalFile("a\x00b", "/x"),
		NewImageBuilder("alpine").AddLocalDir("\xff", "/x"),
	}
	for _, builder := range cases {
		// Given a nil, zero, or invalid recipe.
		// When compiled, then it returns a typed error rather than panicking.
		_, err := builder.Dockerfile()
		require.ErrorIs(t, err, ErrBadRequest)
	}
}
