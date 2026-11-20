// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFromPathRejectsUnsafeDockerfile(t *testing.T) {
	for _, name := range []string{"../Dockerfile", "/Dockerfile", "C:/Dockerfile", "bad\x00name", "bad\xffname", "."} {
		t.Run(name, func(t *testing.T) {
			// Given a caller-owned archive with an invalid Dockerfile path.
			filename := filepath.Join(t.TempDir(), "context.tar")
			require.NoError(t, os.WriteFile(filename, []byte("tar bytes"), 0o600))
			// When prepared.
			result, err := FromPath(t.Context(), filename, name)
			if result != nil {
				t.Cleanup(func() { require.NoError(t, result.Close()) })
			}
			// Then invalid paths cannot reach the upload request.
			var failure *Error
			require.ErrorAs(t, err, &failure)
			require.True(t, failure.Invalid)
		})
	}
}

func TestFromPathNormalizesDockerfile(t *testing.T) {
	// Given a valid relative path with redundant components.
	filename := filepath.Join(t.TempDir(), "context.tar")
	require.NoError(t, os.WriteFile(filename, []byte("tar bytes"), 0o600))
	// When prepared.
	result, err := FromPath(t.Context(), filename, "./build/../Dockerfile")
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, result.Close()) })
	// Then the wire path is canonical.
	require.Equal(t, "Dockerfile", result.Dockerfile)
}

