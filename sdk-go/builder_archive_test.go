// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"archive/tar"
	"errors"
	"io"
	"os"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestBuilderSharedArchive(t *testing.T) {
	var fixture struct {
		Cases        []builderFixture
		Refused      []builderFixture
		BuildRefused []builderFixture
	}
	builderFixtures(t, "image-builder-context.json", &fixture)
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			// Given the shared context tree and recipe.
			root := builderFixtureTree(t, tc)
			builder := builderFromFixture(t, tc, root)
			text, compileErr := builder.Dockerfile()
			require.NoError(t, compileErr)
			require.Equal(t, tc.Dockerfile, text)
			if tc.Entries == nil {
				require.False(t, builder.hasLocalSources())
				return
			}
			// When prepared, then the complete PAX hash and entries agree.
			result, err := builder.prepare(t.Context())
			require.NoError(t, err)
			t.Cleanup(func() { require.NoError(t, result.Close()) })
			require.Equal(t, tc.SHA256, result.Digest)
			file, openErr := os.Open(result.Path)
			require.NoError(t, openErr)
			defer func() { require.NoError(t, file.Close()) }()
			reader := tar.NewReader(file)
			var names []string
			contents := make(map[string]string)
			for {
				header, readErr := reader.Next()
				if errors.Is(readErr, io.EOF) {
					break
				}
				require.NoError(t, readErr)
				names = append(names, header.Name)
				content, contentErr := io.ReadAll(reader)
				require.NoError(t, contentErr)
				contents[header.Name] = string(content)
			}
			require.Equal(t, tc.Entries, names)
			for name, want := range tc.Contents {
				require.Equal(t, want, contents[name], name)
			}
			require.Equal(t, tc.Dockerfile, contents["Dockerfile"])
		})
	}
	for _, tc := range append(fixture.Refused, fixture.BuildRefused...) {
		t.Run(tc.Name, func(t *testing.T) {
			// Given unsafe input from the shared fixtures.
			root := builderFixtureTree(t, tc)
			builder := builderFromFixture(t, tc, root)
			// When prepared, then rejection occurs in the SDK hierarchy.
			result, err := builder.prepare(t.Context())
			require.Nil(t, result)
			require.ErrorIs(t, err, ErrBadRequest)
			require.Contains(t, err.Error(), strings.ReplaceAll(tc.Error, "<root>", root))
		})
	}
}
