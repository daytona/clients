// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"slices"
	"testing"

	"github.com/stretchr/testify/require"

	"go.daytona.com/sdk/v2/internal/dockerignore"
)

func TestDirectorySharedBuildKitCases(t *testing.T) {
	data, err := os.ReadFile("../../../cli/internal/dockerignore/testdata/buildkit_cases.json")
	if errors.Is(err, os.ErrNotExist) && os.Getenv("DAYTONA_SDK_GO_REQUIRE_FIXTURES") == "" {
		t.Skip("shared CLI fixtures are not included in the Go module")
	}
	require.NoError(t, err)
	var cases []struct {
		Ignore   string
		Files    []string
		Uploaded []string
		Fails    bool
	}
	require.NoError(t, json.Unmarshal(data, &cases))
	require.GreaterOrEqual(t, len(cases), 300, "shared BuildKit corpus is truncated")
	for index, tc := range cases {
		t.Run(fmt.Sprintf("case_%03d", index), func(t *testing.T) {
			// Given the same tree and ignore rules as the Python/TS walk tests.
			files := make(map[string]string, len(tc.Files))
			for _, name := range tc.Files {
				files[name] = "fixture"
			}
			root, openErr := os.OpenRoot(fixtureTree(t, treeFixture{Files: files}))
			require.NoError(t, openErr)
			defer func() { require.NoError(t, root.Close()) }()
			patterns, parseErr := dockerignore.ReadPatterns(tc.Ignore)
			require.NoError(t, parseErr)
			matcher, matchErr := dockerignore.New(patterns)
			var walk directoryWalk
			// When the real archive walker filters the tree.
			if matchErr == nil {
				walk = directoryWalk{
					ctx: t.Context(), root: root, filter: dockerignore.NewFilter(matcher, nil),
				}
				matchErr = walk.visit("", true, nil)
			}
			// Then entries and immediate/deferred syntax errors match BuildKit.
			if tc.Fails {
				var failure *dockerignore.Error
				require.ErrorAs(t, matchErr, &failure)
				return
			}
			require.NoError(t, matchErr)
			names := make([]string, 0, len(walk.entries))
			for _, item := range walk.entries {
				names = append(names, item.name)
			}
			slices.Sort(names)
			require.Equal(t, tc.Uploaded, names)
		})
	}
}
