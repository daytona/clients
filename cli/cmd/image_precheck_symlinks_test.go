// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"archive/tar"
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestImageDockerfile_rejectsEscape_whenSymlinkLeavesContext(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlinks need extra privileges on Windows")
	}
	for _, component := range []bool{false, true} {
		t.Run(map[bool]string{false: "file", true: "directory"}[component], func(t *testing.T) {
			// Given a Dockerfile outside the context exposed through a symlink.
			context := t.TempDir()
			outside := t.TempDir()
			require.NoError(t, os.WriteFile(filepath.Join(outside, "Dockerfile"), []byte("FROM scratch"), 0o600))
			name := "Dockerfile"
			if component {
				require.NoError(t, os.Symlink(outside, filepath.Join(context, "build")))
				name = "build/Dockerfile"
			} else {
				require.NoError(t, os.Symlink(filepath.Join(outside, name), filepath.Join(context, name)))
			}
			// When checking the build through the command.
			_, _, err := runCLI(t, []string{"image", "build", "app", "--context", context, "--dockerfile", name}, nil)
			// Then the escape is rejected before any request.
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
			require.ErrorContains(t, err, "inside the build context")
		})
	}
}

func TestImageDockerfile_acceptsInternalSymlink_whenContextItselfIsSymlinked(t *testing.T) {
	// Given a linked context containing an internal linked Dockerfile.
	dir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(dir, "recipe"), []byte("FROM scratch"), 0o600))
	require.NoError(t, os.Symlink("recipe", filepath.Join(dir, "Dockerfile")))
	context := filepath.Join(t.TempDir(), "context")
	require.NoError(t, os.Symlink(dir, context))
	// When checking the resolved context and Dockerfile.
	err := checkImageDockerfile(context, "Dockerfile")
	// Then neither link is mistaken for an escape.
	require.NoError(t, err)
}

func TestImageDockerfile_preservesFilesystemError_whenPathCannotBeResolved(t *testing.T) {
	// Given a missing Dockerfile, not a non-regular one.
	dir := t.TempDir()
	// When checking it.
	err := checkImageDockerfile(dir, "Dockerfile")
	// Then the underlying filesystem cause is retained.
	require.ErrorIs(t, err, os.ErrNotExist)
	require.NotContains(t, err.Error(), "not a regular file")
}

func TestImageDockerfile_acceptsSymlink_whenPlainTarContainsIt(t *testing.T) {
	// Given a tar context with a symlinked Dockerfile; the server resolves archive links.
	var buffer bytes.Buffer
	archive := tar.NewWriter(&buffer)
	require.NoError(t, archive.WriteHeader(&tar.Header{Name: "Dockerfile", Linkname: "recipe", Typeflag: tar.TypeSymlink}))
	require.NoError(t, archive.Close())
	context := filepath.Join(t.TempDir(), "context.tar")
	require.NoError(t, os.WriteFile(context, buffer.Bytes(), 0o600))
	// When checking the archive header.
	err := checkImageDockerfile(context, "Dockerfile")
	// Then it is not incorrectly reported as missing.
	require.NoError(t, err)
}

// A plain tar context larger than a multipart upload (sent directly to
// storage) is still checked for its Dockerfile before any request.
func TestImageDockerfile_checksLargeTarContexts(t *testing.T) {
	path := filepath.Join(t.TempDir(), "context.tar")
	require.NoError(t, os.WriteFile(path, nil, 0o600))
	require.NoError(t, os.Truncate(path, imageContextMaxBytes+1024))
	err := checkImageDockerfile(path, "Dockerfile")
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.Contains(t, err.Error(), "missing from build context")
}

// Tar entry names are matched as the archive extractor reads them: a leading
// "/" or "./" names the same file.
func TestImageDockerfile_matchesRootedTarEntries(t *testing.T) {
	for _, name := range []string{"/Dockerfile", "./Dockerfile", "Dockerfile"} {
		path := filepath.Join(t.TempDir(), "context.tar")
		var buf bytes.Buffer
		tw := tar.NewWriter(&buf)
		require.NoError(t, tw.WriteHeader(&tar.Header{Name: name, Mode: 0o644, Size: 4, Typeflag: tar.TypeReg}))
		_, err := tw.Write([]byte("FROM"))
		require.NoError(t, err)
		require.NoError(t, tw.Close())
		require.NoError(t, os.WriteFile(path, buf.Bytes(), 0o600))
		require.NoError(t, checkImageDockerfile(path, "Dockerfile"), name)
	}
}
