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

func TestFromPathKeepsHashedBytesWhenCallerChangesFile(t *testing.T) {
	for _, change := range []string{"overwrite", "replace"} {
		t.Run(change, func(t *testing.T) {
			// Given an archive hashed before the caller changes its file.
			filename := filepath.Join(t.TempDir(), "context.tar")
			require.NoError(t, os.WriteFile(filename, []byte("original"), 0o600))
			result, err := FromPath(t.Context(), filename, "")
			require.NoError(t, err)
			t.Cleanup(func() { require.NoError(t, result.Close()) })
			if change == "replace" {
				require.NoError(t, os.Rename(filename, filename+".old"))
			}
			require.NoError(t, os.WriteFile(filename, []byte("modified"), 0o600))

			// When the uploader opens the prepared artifact.
			content, err := os.ReadFile(result.Path)

			// Then it reads the hashed bytes, never a new caller-owned baseline.
			require.NoError(t, err)
			require.Equal(t, "original", string(content))
		})
	}
}
