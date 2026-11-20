// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package dockerignore

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestMatcherDefersUnreachedMalformedChunkLikeSiblingSDK(t *testing.T) {
	for _, name := range []string{"included", "other"} {
		t.Run(name, func(t *testing.T) {
			// Given a malformed later chunk unreachable by filepath.Match against ".".
			matcher, err := New([]string{"included", "!x*/["})
			require.NoError(t, err)

			// When evaluating a path with the same lazy exclusion rules as TypeScript.
			excluded, err := matcher.Excluded(name)

			// Then an unused exception is harmless; an evaluated one fails.
			if name == "included" {
				var failure *Error
				require.ErrorAs(t, err, &failure)
				return
			}
			require.NoError(t, err)
			require.False(t, excluded)
		})
	}
}
